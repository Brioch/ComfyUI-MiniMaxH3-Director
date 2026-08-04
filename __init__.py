from comfy_api.latest import ComfyExtension, io
from typing_extensions import override

from .minimax_chain import MiniMaxH3DirectorChain
from .minimax_director import MiniMaxH3Director
from .minimax_preview import MiniMaxH3PreviewOverride
from .minimax_retake import MiniMaxH3RetakeStitch


class MiniMaxH3DirectorExtension(ComfyExtension):
    @override
    async def get_node_list(self) -> list[type[io.ComfyNode]]:
        return [MiniMaxH3Director, MiniMaxH3PreviewOverride,
                MiniMaxH3RetakeStitch, MiniMaxH3DirectorChain]


async def comfy_entrypoint() -> MiniMaxH3DirectorExtension:
    return MiniMaxH3DirectorExtension()


NODE_CLASS_MAPPINGS = {
    "MiniMaxH3DirectorCS": MiniMaxH3Director,
    "MiniMaxH3PreviewOverrideCS": MiniMaxH3PreviewOverride,
    "MiniMaxH3RetakeStitchCS": MiniMaxH3RetakeStitch,
    "MiniMaxH3DirectorChainCS": MiniMaxH3DirectorChain,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MiniMaxH3DirectorCS": "MiniMax H3 Director",
    "MiniMaxH3PreviewOverrideCS": "MiniMax H3 Preview Override",
    "MiniMaxH3RetakeStitchCS": "MiniMax H3 Retake Stitch",
    "MiniMaxH3DirectorChainCS": "MiniMax H3 Director Chain",
}

WEB_DIRECTORY = "./js"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
