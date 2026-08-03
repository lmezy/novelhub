"""Auto-categorization service -- maps novel tags to predefined categories.

Default mapping rules cover common Chinese novel genres. Admins can
customize categories and rules through the API.
"""

import re
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Book, BookTag, Tag
from app.models.book_category import BookCategory
from app.models.category import Category
from app.repositories.category import CategoryRepository
from uuid import uuid4


DEFAULT_CATEGORY_RULES: dict[str, list[str]] = {
    "玄幻": ["玄幻", "异界", "魔法", "斗气", "修真", "修炼"],
    "都市": ["都市", "现代", "校园", "职场", "商战", "现实", "生活"],
    "言情": ["言情", "爱情", "恋爱", "婚恋", "甜宠", "纯爱", "耽美", "百合"],
    "科幻": ["科幻", "星际", "机甲", "未来", "末日", "进化", "变异"],
    "历史": ["历史", "穿越", "古代", "架空", "宫廷", "战争", "三国"],
    "悬疑": ["悬疑", "推理", "侦探", "恐怖", "灵异", "惊悚", "犯罪"],
    "武侠": ["武侠", "江湖", "门派", "武功", "仙侠", "修仙", "剑道"],
    "游戏": ["游戏", "网游", "电竞", "虚拟", "全息", "竞技"],
    "轻小说": ["轻小说", "二次元", "动漫", "同人", "综漫", "穿越"],
    "奇幻": ["奇幻", "魔幻", "西幻", "领主", "种田", "冒险"],
}


class AutoCategorizationService:
    """Automatically assigns categories to books based on their tags."""

    @staticmethod
    async def ensure_default_categories(db: AsyncSession) -> None:
        """Create default categories if they don't exist."""
        repo = CategoryRepository(db)
        colors = {
            "玄幻": "#8B0000", "都市": "#006400", "言情": "#FF69B4",
            "科幻": "#00008B", "历史": "#8B4513", "悬疑": "#4B0082",
            "武侠": "#B8860B", "游戏": "#008080", "轻小说": "#FF4500",
            "奇幻": "#2E8B57",
        }
        for name in DEFAULT_CATEGORY_RULES:
            existing = await repo.get_by_name(name)
            if existing is None:
                await repo.create(name=name, description=f"Auto-generated: {name} novels", color=colors.get(name))

    @staticmethod
    async def categorize_book(db: AsyncSession, book_id: str) -> list[str]:
        """Auto-categorize a single book based on its tags. Returns assigned category names."""
        await AutoCategorizationService.ensure_default_categories(db)
        book = await db.get(Book, book_id)
        if book is None:
            return []
        tags = await db.scalars(
            select(Tag).join(BookTag).where(BookTag.book_id == book_id)
        )
        tag_texts = [t.name.lower() for t in tags]
        if not tag_texts:
            logger.debug("No tags for book {}, skipping auto-categorization", book_id)
            return []
        # Also check title and description
        title_lower = (book.title or "").lower()
        desc_lower = (book.description or "").lower()
        combined = " ".join(tag_texts) + " " + title_lower + " " + desc_lower

        matched_categories = []
        for cat_name, keywords in DEFAULT_CATEGORY_RULES.items():
            for kw in keywords:
                if kw.lower() in combined:
                    matched_categories.append(cat_name)
                    break

        if matched_categories:
            repo = CategoryRepository(db)
            cat_ids = []
            for name in matched_categories:
                cat = await repo.get_by_name(name)
                if cat:
                    cat_ids.append(cat.id)
            if cat_ids:
                await repo.set_book_categories(book_id, cat_ids)
                logger.info("Auto-categorized book {} as {}", book_id, matched_categories)
        return matched_categories

    @staticmethod
    async def categorize_all_books(db: AsyncSession) -> dict:
        """Auto-categorize all uncategorized books in the library."""
        await AutoCategorizationService.ensure_default_categories(db)
        books = await db.scalars(select(Book))
        results = {"total": 0, "categorized": 0, "details": []}
        for book in books:
            results["total"] += 1
            cats = await AutoCategorizationService.categorize_book(db, book.id)
            if cats:
                results["categorized"] += 1
                results["details"].append({"book_id": book.id, "title": book.title, "categories": cats})
        return results
