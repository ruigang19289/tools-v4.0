from django.urls import path, include

urlpatterns = [
    path('ansible/', include('backend.apps.system.ansible.urls')),
    path('system-init/', include('backend.apps.system.system_init.urls')),
    path('hardware-inspection/', include('backend.apps.system.hardware_inspection.urls')),
    path('node-precheck/', include('backend.apps.system.node_precheck.urls')),
]
