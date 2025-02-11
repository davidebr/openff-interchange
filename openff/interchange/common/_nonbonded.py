import abc
from collections.abc import Iterable
from typing import Literal, Union

from openff.models.types import FloatQuantity
from openff.units import Quantity, unit

from openff.interchange.components.potentials import Collection
from openff.interchange.constants import _PME
from openff.interchange.models import LibraryChargeTopologyKey, TopologyKey

try:
    from pydantic.v1 import Field, PrivateAttr
except ImportError:
    from pydantic import Field, PrivateAttr


class _NonbondedCollection(Collection, abc.ABC):  # noqa
    type: str = "nonbonded"

    cutoff: FloatQuantity["angstrom"] = Field(  # noqa
        unit.Quantity(9.0, unit.angstrom),
        description="The distance at which pairwise interactions are truncated",
    )

    scale_12: float = Field(
        0.0,
        description="The scaling factor applied to 1-2 interactions",
    )
    scale_13: float = Field(
        0.0,
        description="The scaling factor applied to 1-3 interactions",
    )
    scale_14: float = Field(
        0.5,
        description="The scaling factor applied to 1-4 interactions",
    )
    scale_15: float = Field(
        1.0,
        description="The scaling factor applied to 1-5 interactions",
    )


class vdWCollection(_NonbondedCollection):
    """Handler storing vdW potentials."""

    type: Literal["vdW"] = "vdW"

    expression: Literal[
        "4*epsilon*((sigma/r)**12-(sigma/r)**6)"
    ] = "4*epsilon*((sigma/r)**12-(sigma/r)**6)"

    mixing_rule: str = Field(
        "lorentz-berthelot",
        description="The mixing rule (combination rule) used in computing pairwise vdW interactions",
    )

    switch_width: FloatQuantity["angstrom"] = Field(  # noqa
        unit.Quantity(1.0, unit.angstrom),
        description="The width over which the switching function is applied",
    )

    periodic_method: Literal["cutoff", "no-cutoff", "pme"] = Field("cutoff")
    nonperiodic_method: Literal["cutoff", "no-cutoff", "pme"] = Field("no-cutoff")

    @classmethod
    def default_parameter_values(cls) -> Iterable[float]:
        """Per-particle parameter values passed to Force.addParticle()."""
        return 1.0, 0.0

    @classmethod
    def potential_parameters(cls) -> Iterable[str]:
        """Return a list of names of parameters included in each potential in this colletion."""
        return "sigma", "epsilon"


class ElectrostaticsCollection(_NonbondedCollection):
    """Handler storing electrostatics interactions."""

    type: Literal["Electrostatics"] = "Electrostatics"

    expression: Literal["coul"] = "coul"

    periodic_potential: Literal[
        "Ewald3D-ConductingBoundary",
        "cutoff",
        "no-cutoff",
    ] = Field(_PME)
    nonperiodic_potential: Literal["Coulomb", "cutoff", "no-cutoff"] = Field("Coulomb")
    exception_potential: Literal["Coulomb"] = Field("Coulomb")

    _charges: dict[
        Union[TopologyKey, LibraryChargeTopologyKey],
        Quantity,
    ] = PrivateAttr(dict())
    _charges_cached: bool = PrivateAttr(False)

    @property
    def charges(self):
        """Get the total partial charge on each atom, including virtual sites."""
        raise NotImplementedError()

    def _get_charges(self):
        """Get the total partial charge on each atom or particle."""
        raise NotImplementedError()
