from django.test import TestCase, override_settings
from django.urls import reverse


class AssetLinksViewTests(TestCase):
    def test_returns_empty_payload_when_settings_missing(self):
        response = self.client.get(reverse("asset-links"))
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(response.content, [])

    @override_settings(
        ANDROID_TWA_PACKAGE_NAME="com.lacolina.taskmanager",
        ANDROID_TWA_SHA256_FINGERPRINTS=["AA:BB", "CC:DD"],
    )
    def test_returns_expected_payload(self):
        response = self.client.get(reverse("asset-links"))
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(
            response.content,
            [
                {
                    "relation": ["delegate_permission/common.handle_all_urls"],
                    "target": {
                        "namespace": "android_app",
                        "package_name": "com.lacolina.taskmanager",
                        "sha256_cert_fingerprints": ["AA:BB", "CC:DD"],
                    },
                }
            ],
        )
