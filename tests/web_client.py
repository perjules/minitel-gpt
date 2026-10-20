"""Browser-like client for existing feature tests; security tests use raw clients."""

def csrf_client(app):
    client = app.test_client()
    with client.session_transaction() as session:
        session["csrf_token"] = "feature-test-csrf-token"
    client.environ_base["HTTP_X_CSRF_TOKEN"] = "feature-test-csrf-token"
    return client
