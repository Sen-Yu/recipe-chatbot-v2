"""출처와 상관없는 공통 레시피 형식.

모든 출처 어댑터는 원본을 이 형식으로 바꾼다. 레코드마다 이용 범위(usage)를 붙여서
RAG 인덱스와 어댑터 학습 데이터를 코드로 나눈다.
"""

from pydantic import BaseModel, Field

from ..schemas import RecipeIngredient, StructuredRecipe


class Usage(BaseModel):
    """이 레코드를 어디에 써도 되는지. 출처 어댑터가 정한다."""

    rag: bool = Field(description="RAG 검색 인덱스에 넣어도 되는지 (출처 링크를 함께 보여줌)")
    train: bool = Field(description="어댑터 학습 데이터에 넣어도 되는지")
    license: str = Field(description="근거가 되는 이용 조건 요약")


class IngredientLine(BaseModel):
    """원본의 재료 한 줄과 해석 결과."""

    raw: str
    name: str
    amount: float | None = None
    unit: str = ""
    grams: float | None = Field(default=None, description="원본에 g·kg 표기가 있을 때만")
    parsed: bool = Field(default=True, description="False면 규칙으로 해석하지 못함 (작은 모델로 보낼 대상)")


class Nutrition(BaseModel):
    """1인분 기준 영양 정보. 출처가 주는 경우만 채운다."""

    kcal: float | None = None
    carbohydrate_g: float | None = None
    protein_g: float | None = None
    fat_g: float | None = None
    sodium_mg: float | None = None
    weight_g: float | None = None


class SourceRecipe(BaseModel):
    source: str = Field(description="출처 어댑터 이름 (예: 10000recipe, foodsafety)")
    source_id: str
    url: str | None = None
    title: str
    description: str = ""
    servings: int | None = None
    cook_time: int | None = Field(default=None, description="분")
    ingredients: list[IngredientLine]
    steps: list[str]
    rating: float | None = None
    rating_count: int | None = None
    published: str | None = None
    nutrition: Nutrition | None = None
    usage: Usage

    @property
    def key(self) -> str:
        return f"{self.source}:{self.source_id}"

    def to_structured(self) -> StructuredRecipe:
        """그래프가 쓰는 정형 레시피로 바꾼다. recipe_id는 '출처:id'."""
        return StructuredRecipe(
            recipe_id=self.key,
            title=self.title,
            servings=max(self.servings or 1, 1),
            cook_time=self.cook_time,
            ingredients=[
                RecipeIngredient(name=i.name, amount=i.amount, unit=i.unit) for i in self.ingredients
            ],
            steps=self.steps,
        )
