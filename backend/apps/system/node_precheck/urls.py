from django.urls import path
from . import views

urlpatterns = [
    path('run', views.run_precheck, name='node_precheck_run'),
    path('artifact/<str:run_id>/<path:artifact_path>', views.download_artifact, name='node_precheck_artifact'),
]
