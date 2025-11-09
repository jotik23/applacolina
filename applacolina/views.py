from __future__ import annotations

from django.conf import settings
from django.http import JsonResponse


def digital_asset_links_view(request):
    """Serve /.well-known/assetlinks.json for Trusted Web Activity verification."""

    package_name = getattr(settings, "ANDROID_TWA_PACKAGE_NAME", "").strip()
    fingerprints = [
        fingerprint.strip()
        for fingerprint in getattr(settings, "ANDROID_TWA_SHA256_FINGERPRINTS", [])
        if fingerprint.strip()
    ]

    if not package_name or not fingerprints:
        return JsonResponse([], safe=False)

    payload = [
        {
            "relation": ["delegate_permission/common.handle_all_urls"],
            "target": {
                "namespace": "android_app",
                "package_name": package_name,
                "sha256_cert_fingerprints": fingerprints,
            },
        }
    ]
    return JsonResponse(payload, safe=False)
