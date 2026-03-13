from muforge.portal.connections.parser import BaseParser


class CoreParser(BaseParser):
    @property
    def core(self):
        return self.connection.core
