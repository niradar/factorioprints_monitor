# urls.py
from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('user/<str:fp_user_id>/', views.user_dashboard, name='user_dashboard'),
    path('user/<str:fp_user_id>/snapshot/', views.take_snapshot_view, name='take_snapshot'),
    path('user/<str:fp_user_id>/comments/', views.comments_between, name='comments_between'),
    path('user/<str:fp_user_id>/recent-comments/', views.recent_comments, name='recent_comments'),
    path('user/<str:fp_user_id>/snapshots/', views.user_snapshots, name='user_snapshots'),
    # New design-system inbox (does not replace the dashboard yet)
    path('user/<str:fp_user_id>/inbox/', views.inbox, name='inbox'),
    path('user/<str:fp_user_id>/inbox/mark-all-done/', views.mark_all_done, name='mark_all_done'),
    path('user/<str:fp_user_id>/blueprints/', views.blueprints_list, name='blueprints'),
    path('user/<str:fp_user_id>/blueprint/<int:blueprint_id>/', views.blueprint_detail, name='blueprint_detail'),
    path(
        'user/<str:fp_user_id>/comment/<int:blueprint_id>/<str:comment_id>/toggle/',
        views.toggle_handled,
        name='toggle_handled',
    ),
]
