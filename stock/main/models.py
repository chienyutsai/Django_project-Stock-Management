from django.db import models
from django.db.models import Q
from django.contrib.auth.models import User

class Watchlist(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    stock_code = models.CharField(max_length=10, null=True, blank=True)
    collected = models.BooleanField(default=False)  # 是否收藏
    quantity = models.IntegerField(null=True, blank=True)  # 購買股數
    average_price = models.FloatField(null=True, blank=True)  # 平均價格
    
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
                check=~Q(quantity=0), name="buyrecord_quantity_nonzero"
            ),
        ]

    def __str__(self):
        return f"{self.user.username} 買 {self.stock_code} {self.quantity} 股"


class Post(models.Model):
    title = models.CharField(max_length=200)
    stock_code = models.CharField(max_length=10, null=True, blank=True)
    content = models.TextField()
    author = models.ForeignKey(User, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title


class Comment(models.Model):
    post = models.ForeignKey(Post, related_name="comments", on_delete=models.CASCADE)
    author = models.ForeignKey(User, on_delete=models.CASCADE)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Comment by {self.author.username}"
    

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

    # 這是你要呼叫的函式 → save_snapshot(...)
    @classmethod
    def save_snapshot(cls, code, name, price, inputs, metrics):
        try:
            cls.objects.create(
                code=code,
                name=name,
                price=price,
                shares=getattr(inputs, "shares_outstanding", None),
                market_cap=getattr(metrics, "market_cap", None),
                pe=getattr(metrics, "pe", None),
                dividend_yield=getattr(metrics, "dividend_yield", None),
            )
        except Exception:
            # 寫入失敗時不影響前端顯示
            pass