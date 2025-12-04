from django.db import models
from decimal import Decimal, InvalidOperation
from django.db.models import Q
from django.contrib.auth.models import User

class Watchlist(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    stock_code = models.CharField(max_length=10, null=True, blank=True)
    collected = models.BooleanField(default=False)  # 是否收藏
    quantity = models.IntegerField(null=True, blank=True)  # 購買股數
    average_price = models.FloatField(null=True, blank=True)  # 平均價格
    position = models.PositiveIntegerField(default=0, db_index=True)

    def __str__(self):
        return f"{self.user.username} - {self.stock_code}"


class BuyRecord(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    stock_code = models.CharField(max_length=10, null=True, blank=True)
    #quantity = models.PositiveIntegerField()
    quantity = models.IntegerField()
    price = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            # 允許正/負，僅禁止 0
            models.CheckConstraint(
                #check=~Q(quantity=0), name="buyrecord_quantity_nonzero"
                condition=~Q(quantity=0),        # ✅ 用 condition，關鍵字參數
                name="buyrecord_quantity_nonzero",
            ),
        ]

    def __str__(self):
        return f"{self.user.username} 買 {self.stock_code} {self.quantity} 股"


class Post(models.Model):
    CATEGORY_CHOICES = [
        ("share", "分享"),
        ("analysis", "分析"),
        ("qa", "問答"),
    ]

    title = models.CharField(max_length=200)
    stock_code = models.CharField(max_length=10, null=True, blank=True)
    content = models.TextField()
    author = models.ForeignKey(User, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    # ⬇⬇ 新增的欄位
    category = models.CharField(
        max_length=20,
        choices=CATEGORY_CHOICES,
        default="share",
        verbose_name="分類",
    )

    def __str__(self):
        return self.title
    

class PostLike(models.Model):
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="likes")
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("post", "user")   # 同一使用者不可重複按讚

    def __str__(self):
        return f"{self.user.username} → {self.post.title}"


class Comment(models.Model):
    post = models.ForeignKey(Post, related_name="comments", on_delete=models.CASCADE)
    author = models.ForeignKey(User, on_delete=models.CASCADE)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Comment by {self.author.username}"
    

class CommentLike(models.Model):
    comment = models.ForeignKey(
        Comment,
        related_name="likes",
        on_delete=models.CASCADE,
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("comment", "user")  # 一個人對同一則留言只能有一個讚

    def __str__(self):
        return f"{self.user.username} → Comment {self.comment_id}"
    

from django.utils import timezone

class StockSnapshot(models.Model):
    code = models.CharField(max_length=10, db_index=True)
    name = models.CharField(max_length=50, null=True, blank=True)
    price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    shares = models.BigIntegerField(null=True, blank=True)
    market_cap = models.DecimalField(max_digits=20, decimal_places=2, null=True, blank=True)
    pe = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    dividend_yield = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @classmethod
    def _to_decimal(cls, v):
        """
        接受：
          - 數字 (int/float/Decimal)
          - 純數字字串 "37080000000000"
          - 帶單位 "37.08 兆" 或 "999.5 億"
        轉成 Decimal 或 None
        """
        if v is None:
            return None

        # 已經是數字就直接包成 Decimal
        if isinstance(v, (int, float, Decimal)):
            return Decimal(str(v))

        s = str(v).strip()
        if not s:
            return None

        # 拿掉逗號
        s = s.replace(",", "")

        multiplier = Decimal("1")
        if s.endswith("兆"):
            multiplier = Decimal("1e12")
            s = s[:-1].strip()
        elif s.endswith("億"):
            multiplier = Decimal("1e8")
            s = s[:-1].strip()

        # 這裡 s 應該只剩數字了
        return Decimal(s) * multiplier

    @classmethod
    def save_snapshot(cls, code, name, price, inputs, metrics):
        try:
            cls.objects.create(
                code=code,
                name=name,
                price=price,
                shares=getattr(inputs, "shares_outstanding", None),
                market_cap=metrics.get("市值_raw"),
                pe=metrics.get("本益比_raw"),
                dividend_yield=metrics.get("股息殖利率_raw"),
            )
        except Exception:
            pass