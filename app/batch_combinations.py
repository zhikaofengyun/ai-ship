"""Deterministic selection of source intervals and generator alternatives for a batch."""

import math
from functools import lru_cache

from .store import digest


def dimensions(slots):
    return tuple((len(slot.choices), bool(slot.generate)) for slot in slots)


@lru_cache(maxsize=256)
def permutation(shape, seed):
    # Retain the mixed-radix permutation used by existing frozen recipes.
    radices = tuple(choices + generate for choices, generate in shape)
    size = math.prod(radices)
    stride = int(digest([seed, "stride"])[:16], 16) % size or 1
    while math.gcd(stride, size) != 1:
        stride += 1
    generator_indices = tuple(i for i, (_, generate) in enumerate(shape) if generate)
    return radices, size, stride, generator_indices


def positions_for(shape, seed, ordinal):
    radices, size, stride, generators = permutation(shape, seed)
    cycle, offset = divmod(ordinal - 1, size)
    rank = (seed + offset * stride) % size
    positions = []
    for radix in radices:
        rank, position = divmod(rank, radix)
        positions.append(position)
    if cycle and generators and all(positions[i] < shape[i][0] for i in generators):
        # Each fully sourced sequence is used once. Later passes always include a new
        # seeded generation, rotating which eligible shot is generated on successive passes.
        i = generators[(offset + cycle - 1) % len(generators)]
        positions[i] = shape[i][0]
    return positions


def choices_for(recipe, ordinal):
    positions = positions_for(dimensions(recipe.slots), recipe.seed, ordinal)
    return [
        slot.choices[pos] if pos < len(slot.choices) else None
        for slot, pos in zip(recipe.slots, positions)
    ]


@lru_cache(maxsize=256)
def generation_counts(shape, seed, count):
    """Count the exact planned submissions without loading sources or contacting a model."""
    results = [count if generate and not choices else 0 for choices, generate in shape]
    mixed = [i for i, (choices, generate) in enumerate(shape) if choices and generate]
    if mixed:
        for ordinal in range(1, count + 1):
            positions = positions_for(shape, seed, ordinal)
            for i in mixed:
                if positions[i] == shape[i][0]:
                    results[i] += 1
    return tuple(results)


def item_generation_flags(recipe, workflow_calls):
    shape = dimensions(recipe.slots)
    if workflow_calls or any(generate and not choices for choices, generate in shape):
        yield from (1 for _ in range(recipe.count))
    elif not any(generate for _, generate in shape):
        yield from (0 for _ in range(recipe.count))
    else:
        for ordinal in range(1, recipe.count + 1):
            positions = positions_for(shape, recipe.seed, ordinal)
            yield int(any(pos == choices for pos, (choices, _) in zip(positions, shape)))
