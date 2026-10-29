"""Bind private services to the selected profile; never accept model-selected identity."""
try:
    from . import gmail_auth, gmail_client, user_registry
except ImportError:
    import gmail_auth
    import gmail_client
    import user_registry


class ProfileTokens:
    def __init__(self, user, registry):
        self.user, self.registry = user, registry
        self.binding = None
        self.source = None
        self.email_address = None
        self.credentials = None

    def guard(self, operation='read'):
        if self.registry.get(self.user.id).revision != self.user.revision:
            raise gmail_auth.GmailError('account_assignment_changed')
        binding = self.registry.gmail(self.user.id, operation, self.binding)
        if self.binding is None:
            self.binding = binding
        return binding

    def token(self, deadline):
        _, account, revision = self.guard()
        if self.source is None:
            self.source = gmail_auth.AccountTokenSource(account, self.guard, revision)
        value = self.source.token(deadline)
        self.email_address = self.source.email_address
        return value


class ProfileMailClient(gmail_client.GmailClient):
    def __init__(self, user, registry):
        super().__init__(tokens=ProfileTokens(user, registry))

    def request(self, method, path, deadline, params=None, payload=None):
        self.tokens.guard('send' if method == 'POST' else 'read')
        return super().request(method,path,deadline,params,payload)


def mail_client(name='', root=None):
    registry = user_registry.Registry(root)
    return ProfileMailClient(registry.resolve(name), registry)
