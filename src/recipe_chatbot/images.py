"""완성 사진 생성 인터페이스 (설계 문서 4.6).

지금 단계는 외부 이미지 모델을 쓸 예정이라 인터페이스만 둔다. 제공사가 정해지면 구현을 추가한다.
"""

from typing import Protocol

from .schemas import StructuredRecipe

AI_IMAGE_NOTICE = "AI가 생성한 예시 이미지이며 실제와 다를 수 있어요."


class ImageGenerator(Protocol):
    def generate(self, recipe: StructuredRecipe) -> str | None:
        """이미지 URL을 돌려준다. 실패하면 None (레시피 응답은 그대로 끝난다)."""
        ...


class NullImageGenerator:
    def generate(self, recipe: StructuredRecipe) -> str | None:
        return None


def image_prompt(recipe: StructuredRecipe) -> str:
    """요리 이름보다 생김새를 묘사하는 프롬프트. 사용자 원문 발화는 넣지 않는다."""
    main = ", ".join(i.name for i in recipe.ingredients[:5])
    return f"완성된 {recipe.title} 한 그릇, 주재료 {main}, 위에서 비스듬히 찍은 음식 사진"
