from django.db import models
from django.contrib.auth.models import User

class Watchlist(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    stock_code = models.CharField(max_length=10)
    collected = models.BooleanField(default=False)  # 是否收藏
    quantity = models.IntegerField(null=True, blank=True)  # 購買股數
    average_price = models.FloatField(null=True, blank=True)  # 平均價格
    
    def __str__(self):
        return f"{self.user.username} - {self.stock_code}"


class BuyRecord(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    stock_code = models.CharField(max_length=20)
    quantity = models.PositiveIntegerField()
    price = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} 買 {self.stock_code} {self.quantity} 股"


class Post(models.Model):
    title = models.CharField(max_length=200)
    stock_code = models.CharField(max_length=20)
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