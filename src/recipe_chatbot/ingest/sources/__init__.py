from .base import Source
from .foodsafety import FoodSafety
from .manrecipe import ManRecipe

SOURCES: dict[str, type[Source]] = {ManRecipe.name: ManRecipe, FoodSafety.name: FoodSafety}

__all__ = ["SOURCES", "FoodSafety", "ManRecipe", "Source"]
