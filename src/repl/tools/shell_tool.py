import time

import asyncio
import logging
import mcp.types as types
import os
import pathlib
import uuid
from typing import List, Dict, Optional

from repl.tools.base import BaseTool, CodeOutput

# Configure logging
logger = logging.getLogger('shell_tool')
logger.setLevel(logging.DEBUG)

# Create handlers
log_file = pathlib.Path('/mnt/d/Users/HauHau/PycharmProjects/claude/repl/logs/shell_tool.log')
file_handler = logging.FileHandler(log_file)
file_handler.setLevel(logging.DEBUG)

# Create formatters and add it to handlers
log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
file_handler.setFormatter(logging.Formatter(log_format))

# Add handlers to the logger
logger.addHandler(file_handler)


class ShellTask:
    def __init__(self, command: str, shell: str, working_dir: str):
        self.id = str(uuid.uuid4())
        self.command = command
        self.shell = shell
        self.working_dir = working_dir
        self.process: Optional[asyncio.subprocess.Process] = None
        self.status = "pending"  # pending, running, completed, failed
        self.stdout = ""
        self.stderr = ""
        self.result = None
        self.execution_time = None
        self.start_time = None


class ShellTool(BaseTool):
    """Tool for executing shell commands with automatic async fallback"""

    SYNC_TIMEOUT = 5.0  # Switch to async mode if command doesn't complete within 5 seconds

    def __init__(self):
        self.tasks: Dict[str, ShellTask] = {}

    @property
    def name(self) -> str:
        return "shell"

    @property
    def description(self) -> str:
        return """Execute shell commands with automatic async fallback.

If the command completes within 5 seconds, you'll get the result immediately.
If it takes longer, you'll get a task ID that you can use to check status with shell_status.

Example responses:
1. Quick command:
   Standard Output: <output>
   Standard Error: <error>
   Return Value: 0

2. Long-running command:
   Task started with ID: 1234-5678-90
   Use shell_status with this task ID to check progress."""

    @property
    def schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "shell": {
                    "type": "string",
                    "description": "Shell to use (bash/sh/zsh)",
                    "default": "bash",
                    "enum": ["bash", "sh", "zsh"]
                },
                "working_dir": {
                    "type": "string",
                    "description": "Working directory to execute the command in (defaults to user home)",
                    "default": ""
                },
                "command": {
                    "type": "string",
                    "description": "Shell command to execute"
                }
            },
            "required": ["command"]
        }

    async def execute(self, arguments: dict) -> List[types.TextContent]:
        command = arguments.get("command")
        if not command:
            raise ValueError("Missing command parameter")

        shell = arguments.get("shell", "bash")
        working_dir = arguments.get("working_dir")

        if not working_dir:
            working_dir = pathlib.Path.home()

        # Verify working directory exists
        if working_dir and not os.path.exists(working_dir):
            raise ValueError(f"Working directory does not exist: {working_dir}")

        # Create task
        task = ShellTask(command, shell, working_dir)
        self.tasks[task.id] = task
        logger.info(f"Created task {task.id} for command: {command}")

        try:
            # Try to execute synchronously with timeout
            result = await asyncio.wait_for(
                self._execute_task(task.id),
                timeout=self.SYNC_TIMEOUT
            )

            # If we get here, command completed within timeout
            output_text = ""
            if result.stdout:
                output_text += f"Standard Output:\n{result.stdout}\n"
            if result.stderr:
                output_text += f"Standard Error:\n{result.stderr}\n"
            output_text += f"Execution time: {result.execution_time:.4f} seconds\n"
            output_text += f"Return Value:\n{result.result}"

            return [types.TextContent(
                type="text",
                text=output_text
            )]

        except asyncio.TimeoutError:
            # Command is taking too long, switch to async mode
            logger.info(f"Command taking longer than {self.SYNC_TIMEOUT}s, switching to async mode")

            # Make sure the task continues running in the background
            asyncio.create_task(self._execute_task(task.id))

            return [types.TextContent(
                type="text",
                text=f"Task started with ID: {task.id}\nUse shell_status with this task ID to check progress."
            )]

    async def _execute_task(self, task_id: str) -> CodeOutput:
        task = self.tasks[task_id]
        output = CodeOutput()
        task.status = "running"
        task.start_time = time.time()

        try:
            logger.debug(f"Creating subprocess for task {task_id}")
            task.process = await asyncio.create_subprocess_exec(
                task.shell,
                "-c",
                task.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=task.working_dir
            )

            logger.info(f"Process created with PID: {task.process.pid}")

            # Wait for the command to complete and capture output
            stdout, stderr = await task.process.communicate()

            # Store results
            output.stdout = stdout.decode() if stdout else ""
            output.stderr = stderr.decode() if stderr else ""
            output.result = task.process.returncode

            # Update task status
            task.stdout = output.stdout
            task.stderr = output.stderr
            task.result = output.result
            task.status = "completed"
            task.execution_time = time.time() - task.start_time
            output.execution_time = task.execution_time

            logger.info(f"Task {task_id} completed with return code: {task.result}")
            if output.stderr:
                logger.warning(f"Task {task_id} stderr output: {output.stderr}")

        except Exception as e:
            error_msg = f"Error executing command: {str(e)}"
            logger.error(error_msg, exc_info=True)
            output.stderr = error_msg
            task.stderr = error_msg
            task.status = "failed"
            task.execution_time = time.time() - task.start_time
            output.execution_time = task.execution_time
            output.result = -1
            task.result = -1

        return output