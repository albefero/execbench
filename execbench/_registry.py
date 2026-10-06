# Makes the tasks discoverable as `execbench/execbench` once the package is installed.
from execbench.task import execbench, execbench_baseline  # noqa: F401

from inspect_ai.model import modelapi


@modelapi(name="codex_cli")
def codex_cli():
    from execbench.cli_provider import CodexCLI

    return CodexCLI


@modelapi(name="claude_cli")
def claude_cli():
    from execbench.cli_provider import ClaudeCLI

    return ClaudeCLI
