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

    @property
    def credentials(self):
        return self.source.credentials if self.source else None

    @credentials.setter
    def credentials(self,value):
        if self.source:
            self.source.credentials=value

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
        operation='send' if method == 'POST' else 'read'
        outcome='denied'
        try:
            self.tokens.guard(operation)
            result=super().request(method,path,deadline,params,payload)
            outcome='ok'
            return result
        finally:
            self.tokens.registry.audit(self.tokens.user.id,
                self.tokens.binding[1] if self.tokens.binding else None,operation,outcome)


def mail_client(name='', root=None):
    registry = user_registry.Registry(root)
    return ProfileMailClient(registry.resolve(name), registry)


class ProfileSchool:
    def __init__(self, backend, user, registry):
        self.backend, self.user, self.registry = backend, user, registry

    def config(self):
        config = self.backend.config()
        try:
            allowed = self.registry.children(self.user.id) if self.registry.get(self.user.id).revision == self.user.revision else set()
        except gmail_auth.GmailError:
            allowed = set()
        return {**config, 'children':{k:v for k,v in config['children'].items() if k in allowed}}

    def tools(self):
        import copy
        children = self.config()['children']
        if not children:
            return []
        tools = copy.deepcopy(self.backend.tools())
        for tool in tools:
            prop = tool['function']['parameters']['properties'].get('child')
            if prop is not None:
                prop['enum'] = list(children)
        return tools

    def execute(self,name,arguments,deadline=None):
        import json
        if isinstance(arguments,str):
            try:
                arguments=json.loads(arguments)
            except ValueError:
                return {'error':'invalid_arguments'}
        allowed=self.config()['children']
        if name=='pronote_list_children' and arguments=={}:
            return {'children':list(allowed)}
        if not isinstance(arguments,dict) or arguments.get('child') not in allowed:
            return {'error':'permission_denied'}
        return self.backend.execute(name,arguments,deadline)
