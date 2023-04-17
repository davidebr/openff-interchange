from typing import Dict, List, Union, Any

class Forcefield(object):
    def __init__(self, name: int): ...
    def get_parameters(
        self,
        group: str,
        key: Union[str, List[str]],
        keys_are_atom_classes: bool = False,
    ) -> Dict[str, float]: ...
    @staticmethod
    def get_generator(ff: Forcefield, gen_type: Any) -> Any: ...
    @property
    def lj14scale(self) -> float: ...
    @property
    def coulomb14scale(self) -> float: ...
