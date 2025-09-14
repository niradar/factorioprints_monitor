# urls.py
from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('user/<str:fp_user_id>/', views.user_dashboard, name='user_dashboard'),
    path('user/<str:fp_user_id>/snapshot/', views.take_snapshot_view, name='take_snapshot'),
    path('user/<str:fp_user_id>/comments/', views.comments_between, name='comments_between'),
    path('user/<str:fp_user_id>/snapshots/', views.user_snapshots, name='user_snapshots'),
]
