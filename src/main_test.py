import unittest

# import all test cases, otherwise unittest won't find them
from tests.dict_helper import (
    DropUndefinedUseCase, 
    DropExceptKeysUseCase, 
    AsDictDataclassUseCase
)
if __name__ == "__main__":
    unittest.main()