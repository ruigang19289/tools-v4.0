from django.urls import path
from . import views
urlpatterns = [path('inspect', views.inspect, name='hardware_inspection')]
