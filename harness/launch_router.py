"""Routes one parsed launch to the dispatcher, which runs every agent: producers, the reviewer and the writer."""
from harness.launch_tools import Launch


class LaunchRouter:
    def __init__(self, dispatcher, view):
        self._dispatcher, self._view = dispatcher, view

    def execute(self, launch: Launch):
        result = self._dispatcher.dispatch(launch.agent, target=launch.target, feedback=launch.feedback,
                                           terminal_state=launch.terminal_state)
        return self._view.result(launch.agent, result) if result["ok"] else result
