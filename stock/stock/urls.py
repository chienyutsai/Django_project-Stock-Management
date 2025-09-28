from django.contrib import admin
from django.urls import path, include  # 加入 include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('main.urls')),     # 導入 main app 的路由
]
