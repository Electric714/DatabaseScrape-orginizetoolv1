from app.crawler import _has_access_challenge


def test_real_access_challenge_is_detected_near_document_start():
    html = "<html><head><title>Access Denied</title></head><body>Verify that you are human</body></html>"
    assert _has_access_challenge(html)


def test_late_user_content_does_not_become_false_access_challenge():
    html = "<html><head><title>Business complaints</title></head><body>" + ("normal-content " * 4000)
    html += "The customer says the portal showed access denied and then a captcha.</body></html>"
    assert not _has_access_challenge(html)
