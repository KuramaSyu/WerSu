# Style guide for doc strings and comments
This project uses Google Style doc strings in combination with Sphinx.
This means a doc string look like this:

```py
class Config(ABC):
    """an abstract base class without even having an abstract method. But it has 
    2 implementations:
    * :class:`Json`
    * :class:`Yaml`
    
    Note:
        This way its easy to find the Interface-implementations for classes
    """

    def combine(self, other: Self) -> Self:
        """
        combines 2 configs into one. 

        Args:
            other: an object of the same type as `` `self` ``
        """
class Json(Config):
    """does something"""

class Yaml(Config):
    def from_json(json: Json) -> Yaml:
        """
        Args:
            json: a :class:`Json` object which should be converted

        Raises:
            :exc:`ValueError` The JSON is invalid

        Returns:
            :class:`Yaml`: the new Yaml

        Note:
            :class:`Yaml` links to a class. 
        """

    def maybe_from_json(json: UndefinedOr[Json]) -> Optional[Json]:
        """
        Args:
            json: a :class:`Json` object or :obj:`~api.UNDEFINED` 

        Raises:
            :exc:`ValueError` The JSON is invalid

        Returns:
            :class:`Yaml`: the new Yaml

        Note:
            :obj:`~api.UNDEFINED` renders as a link `UNDEFINED` without the api part 
        """

class 
```

### What to document
What to document and how heavily:

type | description
-----|--------------
Abstract Base Classes | These most often life in the :mod:`api` directory. Here every single arg should be documented as well as errors. In the class doc string also add which implementations it has with the `` :class:`ClassName` `` way.
Class doc strings | Always document them. Especially what the class is used for
Implementations | Generally keep it simple there. If it does nothing special, then don't write them at all. Only Class docstrings. In case, there is a `_private_method`, then document what is does. Only methods which belong to a documented abstract method can be simple. 
Helper functions | Just what they do
Errors within any method or function | Always document them  
Inline Comments | Always. but small and compact. They nearly never should span over multiple lines, if they don't have a good reason for that. Only if theres a bug or something serious which is worth explaining over multiple lines