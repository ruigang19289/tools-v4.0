from django.http import JsonResponse


def health(request):
    """健康检查"""
    return JsonResponse({
        'status': 'healthy',
        'service': 'tools-backend',
    })


def info(request):
    """服务信息"""
    import os
    from config import VDBENCH_RESULT_DIR, DATA_DIR, LOGS_DIR

    return JsonResponse({
        'service': 'tools-backend',
        'version': '3.0.0',
        'paths': {
            'data': str(DATA_DIR),
            'vdbench_result': str(VDBENCH_RESULT_DIR),
            'logs': str(LOGS_DIR),
        }
    })
