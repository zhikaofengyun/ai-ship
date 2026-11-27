"""Compute-only smoke with the Hugging Face tiny SD checkpoint; no production quality claim."""

import os
from functools import lru_cache
import numpy as np
import torch
from PIL import Image
from diffusers import (
    AutoencoderKL,
    AnimateDiffVideoToVideoPipeline,
    DDIMScheduler,
    MotionAdapter,
    UNet2DConditionModel,
)
from transformers import CLIPTextModel, CLIPTokenizer


@lru_cache(maxsize=1)
def pipeline():
    source = os.environ["QA_TINY_SD"]
    unet = UNet2DConditionModel.from_pretrained(source, subfolder="unet", local_files_only=True)
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(0)
        motion = MotionAdapter(
            block_out_channels=tuple(unet.config.block_out_channels),
            motion_layers_per_block=unet.config.layers_per_block,
            motion_norm_num_groups=unet.config.norm_num_groups,
            motion_num_attention_heads=4,
        )
    return AnimateDiffVideoToVideoPipeline(
        unet=unet,
        motion_adapter=motion,
        vae=AutoencoderKL.from_pretrained(source, subfolder="vae", local_files_only=True),
        text_encoder=CLIPTextModel.from_pretrained(
            source, subfolder="text_encoder", local_files_only=True
        ),
        tokenizer=CLIPTokenizer.from_pretrained(
            source, subfolder="tokenizer", local_files_only=True
        ),
        scheduler=DDIMScheduler.from_pretrained(
            source, subfolder="scheduler", local_files_only=True
        ),
    )


class TinyDiffusion:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "text": ("STRING",),
                "seed": ("INT", {"default": 0, "min": 0, "max": 2147483647}),
            }
        }

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "compute"
    CATEGORY = "qa"

    def compute(self, image, text, seed):
        still = Image.fromarray(
            (image[0].cpu().numpy() * 255).round().clip(0, 255).astype(np.uint8)
        )
        pipe = pipeline()
        out = pipe(
            prompt=text,
            video=[still] * 4,
            width=64,
            height=64,
            num_inference_steps=2,
            strength=1.0,
            guidance_scale=2,
            generator=torch.Generator(device="cpu").manual_seed(seed),
            output_type="np",
        )
        return (torch.from_numpy(np.ascontiguousarray(out.frames[0])),)


NODE_CLASS_MAPPINGS = {"QATinyDiffusion": TinyDiffusion}
