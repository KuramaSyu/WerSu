from enum import Flag, auto

class RolePermission(Flag):
    """
    Represents one Permission for 
    a specific note or globally
    """
    READ = auto()       #0b0001
    WRITE = auto()      #0b0010
    EXECUTE = auto()    #0b0100
    ALL = READ | WRITE | EXECUTE