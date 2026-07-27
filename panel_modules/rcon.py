"""RCON client boundary. Protocol implementation remains isolated here."""
class RconClient:
    def __init__(self, host="127.0.0.1", port=25575, password=""): self.host,self.port,self.password=host,port,password
