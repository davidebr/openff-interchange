import warnings
from copy import deepcopy
from pathlib import Path
from typing import Dict, Optional, Union

import mdtraj as md
import numpy as np
from openff.toolkit.topology.topology import Topology
from openff.toolkit.typing.engines.smirnoff import ForceField
from openff.toolkit.typing.engines.smirnoff.parameters import (
    AngleHandler,
    BondHandler,
    ConstraintHandler,
    ImproperTorsionHandler,
    ProperTorsionHandler,
    vdWHandler,
)
from pydantic import Field, validator

from openff.system.components.mdtraj import OFFBioTop
from openff.system.components.potentials import PotentialHandler
from openff.system.components.smirnoff import (
    SMIRNOFFAngleHandler,
    SMIRNOFFBondHandler,
    SMIRNOFFConstraintHandler,
    SMIRNOFFElectrostaticsHandler,
    SMIRNOFFImproperTorsionHandler,
    SMIRNOFFProperTorsionHandler,
    SMIRNOFFvdWHandler,
)
from openff.system.exceptions import (
    InternalInconsistencyError,
    InvalidBoxError,
    InvalidTopologyError,
    MissingPositionsError,
    UnsupportedExportError,
)
from openff.system.models import DefaultModel
from openff.system.types import ArrayQuantity

_SUPPORTED_SMIRNOFF_HANDLERS = {
    "Constraints",
    "Bonds",
    "Angles",
    "ProperTorsions",
    "ImproperTorsions",
    "vdW",
    "Electrostatics",
    "LibraryCharges",
    "ChargeIncrementModel",
}

_SMIRNOFF_HANDLER_MAPPINGS = {
    ConstraintHandler: SMIRNOFFConstraintHandler,
    BondHandler: SMIRNOFFBondHandler,
    AngleHandler: SMIRNOFFAngleHandler,
    ProperTorsionHandler: SMIRNOFFProperTorsionHandler,
    ImproperTorsionHandler: SMIRNOFFImproperTorsionHandler,
    vdWHandler: SMIRNOFFvdWHandler,
}


class System(DefaultModel):
    """
    A molecular system object.

    .. warning :: This object is in an early and experimental state and unsuitable for production.
    .. warning :: This API is experimental and subject to change.
    """

    class InnerSystem(DefaultModel):
        handlers: Dict[str, PotentialHandler] = dict()
        topology: Optional[OFFBioTop] = Field(None)
        box: ArrayQuantity["nanometer"] = Field(None)  # type: ignore
        positions: ArrayQuantity["nanometer"] = Field(None)  # type: ignore

        @validator("box")
        def validate_box(cls, val):
            if val is None:
                return val
            if val.shape == (3, 3):
                return val
            elif val.shape == (3,):
                val = val * np.eye(3)
                return val
            else:
                raise InvalidBoxError

    def __init__(self):
        self._inner_data = self.InnerSystem()

    @property
    def handlers(self):
        return self._inner_data.handlers

    def add_handler(self, handler_name: str, handler):
        self._inner_data.handlers.update({handler_name: handler})

    def remove_handler(self, handler_name: str):
        self._inner_data.handlers.pop(handler_name)

    @property
    def topology(self):
        return self._inner_data.topology

    @topology.setter
    def topology(self, value):
        self._inner_data.topology = value

    @property
    def positions(self):
        return self._inner_data.positions

    @positions.setter
    def positions(self, value):
        self._inner_data.positions = value

    @property
    def box(self):
        return self._inner_data.box

    @box.setter
    def box(self, value):
        self._inner_data.box = value

    @classmethod
    def _check_supported_handlers(cls, force_field: ForceField):

        unsupported = list()

        for handler in force_field.registered_parameter_handlers:
            if handler in {"ToolkitAM1BCC"}:
                continue
            if handler not in _SUPPORTED_SMIRNOFF_HANDLERS:
                unsupported.append(handler)

        if unsupported:
            from openff.system.exceptions import SMIRNOFFHandlersNotImplementedError

            raise SMIRNOFFHandlersNotImplementedError(unsupported)

    @classmethod
    def from_smirnoff(
        cls,
        force_field: ForceField,
        topology: OFFBioTop,
        box=None,
    ) -> "System":
        """Creates a new system object by parameterizing a topology using the specified
        SMIRNOFF force field and

        Parameters
        ----------
        force_field
            The force field to parameterize the topology with.
        topology
            The topology to parameterize.
        box
            The box vectors associated with the system.
        """
        sys_out = System()

        cls._check_supported_handlers(force_field)

        if isinstance(topology, OFFBioTop):
            sys_out.topology = topology
        elif isinstance(topology, Topology):
            sys_out.topology = OFFBioTop(other=topology)
            sys_out.topology.mdtop = md.Topology.from_openmm(topology.to_openmm())
        else:
            raise InvalidTopologyError(
                "Could not process topology argument, expected Topology or OFFBioTop. "
                f"Found object of type {type(topology)}."
            )

        parameter_handlers_by_type = {
            force_field[parameter_handler_name].__class__: force_field[
                parameter_handler_name
            ]
            for parameter_handler_name in force_field.registered_parameter_handlers
        }

        # TODO: Wrap electrosotatics logic into this loop (#200)
        for potential_handler_type in [
            SMIRNOFFAngleHandler,
            SMIRNOFFBondHandler,
            SMIRNOFFImproperTorsionHandler,
            SMIRNOFFProperTorsionHandler,
            SMIRNOFFvdWHandler,
            SMIRNOFFElectrostaticsHandler,
        ]:

            parameter_handlers = [
                parameter_handlers_by_type[allowed_type]
                for allowed_type in potential_handler_type.allowed_parameter_handlers()
                if allowed_type in parameter_handlers_by_type
            ]

            if len(parameter_handlers) == 0:
                continue

            # TODO: Might be simpler to rework the bond handler to be self-contained and move back
            # to the constraint handler dealing with the logic (and depending on the bond handler)
            if potential_handler_type == SMIRNOFFBondHandler:
                if "Constraints" in force_field.registered_parameter_handlers:
                    constraint_handler = force_field["Constraints"]
                else:
                    constraint_handler = None
                potential_handler, constraints = SMIRNOFFBondHandler._from_toolkit(
                    bond_handler=force_field["Bonds"],
                    topology=topology,
                    constraint_handler=constraint_handler,
                )
                sys_out.handlers.update({"Bonds": potential_handler})
                if constraint_handler is not None:
                    sys_out.handlers.update({"Constraints": constraints})
            elif potential_handler_type == SMIRNOFFElectrostaticsHandler:
                potential_handler = SMIRNOFFElectrostaticsHandler._from_toolkit(
                    parameter_handler=parameter_handlers,
                    topology=topology,
                )
            else:
                potential_handler = potential_handler_type._from_toolkit(  # type: ignore
                    parameter_handler=parameter_handlers[0],
                    topology=topology,
                )

            sys_out.handlers.update({potential_handler.type: potential_handler})

        # `box` argument is only overriden if passed `None` and the input topology
        # has box vectors
        if box is None and topology.box_vectors is not None:
            sys_out.box = topology.box_vectors
        else:
            sys_out.box = box

        return sys_out

    def to_gro(self, file_path: Union[Path, str], writer="internal", decimal: int = 8):
        """Export this system to a .gro file using ParmEd"""

        if self.positions is None:
            raise MissingPositionsError(
                "Positions are required to write a `.gro` file but found None."
            )
        elif np.allclose(self.positions, 0):
            warnings.warn(
                "Positions seem to all be zero. Result coordinate file may be non-physical.",
                UserWarning,
            )

        # TODO: Enum-style class for handling writer arg?
        if writer == "parmed":
            from openff.system.interop.external import ParmEdWrapper

            ParmEdWrapper().to_file(self, file_path)

        elif writer == "internal":
            from openff.system.interop.internal.gromacs import to_gro

            to_gro(self, file_path, decimal=decimal)

    def to_top(self, file_path: Union[Path, str], writer="internal"):
        """Export this system to a .top file using ParmEd"""
        if writer == "parmed":
            from openff.system.interop.external import ParmEdWrapper

            ParmEdWrapper().to_file(self, file_path)

        elif writer == "internal":
            from openff.system.interop.internal.gromacs import to_top

            to_top(self, file_path)

    def to_lammps(self, file_path: Union[Path, str], writer="internal"):
        if writer != "internal":
            raise UnsupportedExportError

        from openff.system.interop.internal.lammps import to_lammps

        to_lammps(self, file_path)

    def to_openmm(self, combine_nonbonded_forces: bool = False):
        """Export this system to an OpenMM System"""
        from openff.system.interop.openmm import to_openmm as to_openmm_

        return to_openmm_(self, combine_nonbonded_forces=combine_nonbonded_forces)

    def to_prmtop(self, file_path: Union[Path, str], writer="parmed"):
        """Export this system to an Amber .prmtop file"""
        if writer == "parmed":
            from openff.system.interop.external import ParmEdWrapper

            ParmEdWrapper().to_file(self, file_path)

        else:
            raise UnsupportedExportError

    def to_crd(self, file_path: Union[Path, str], writer="parmed"):
        """Export this system to an Amber .crd file"""
        if writer == "parmed":
            from openff.system.interop.external import ParmEdWrapper

            ParmEdWrapper().to_file(self, file_path)

        else:
            raise UnsupportedExportError

    def _to_parmed(self):
        """Export this system to a ParmEd Structure"""
        from openff.system.interop.parmed import _to_parmed

        return _to_parmed(self)

    @classmethod
    def _from_parmed(cls, structure):
        from openff.system.interop.parmed import _from_parmed

        return _from_parmed(cls, structure)

    def _get_nonbonded_methods(self):
        if "vdW" in self.handlers:
            nonbonded_handler = "vdW"
        elif "Buckingham-6" in self.handlers:
            nonbonded_handler = "Buckingham-6"
        else:
            raise InternalInconsistencyError("Found no non-bonded handlers")

        nonbonded_ = {
            "electrostatics_method": self.handlers["Electrostatics"].method,
            "vdw_method": self.handlers[nonbonded_handler].method,
            "periodic_topology": self.box is not None,
        }

        return nonbonded_

    # TODO: Does this cause any strange behaviors with Pydantic?
    # Taken from https://stackoverflow.com/a/4017638/4248961
    _aliases = {"box_vectors": "x", "coordinates": "positions", "top": "topology"}

    def __setattr__(self, name, value):
        name = self._aliases.get(name, name)
        object.__setattr__(self, name, value)

    def __getattr__(self, name):
        name = self._aliases.get(name, name)
        return object.__getattribute__(self, name)

    def __getitem__(self, item: str):
        """Syntax sugar for looking up potential handlers or other components"""
        if type(item) != str:
            raise LookupError(
                "Only str arguments can be currently be used for lookups.\n"
                f"Found item {item} of type {type(item)}"
            )
        if item == "positions":
            return self.positions
        elif item in {"box", "box_vectors"}:
            return self.box
        elif item in self.handlers:
            return self.handlers[item]
        else:
            raise LookupError(
                f"Could not find component {item}. This object has the following "
                f"potential handlers registered:\n\t{[*self.handlers.keys()]}"
            )

    def __add__(self, other):
        """Combine two System objects. This method is unstable and likely unsafe."""
        import mdtraj as md

        from openff.system.models import TopologyKey

        warnings.warn(
            "System combination is experimental and likely to produce strange results. "
            "Use with caution!"
        )

        self_copy = System()
        self_copy._inner_data = self._inner_data

        atom_offset = self_copy.topology.mdtop.n_atoms

        other_top = deepcopy(other.topology)

        for top_mol in other_top.topology_molecules:
            self_copy.topology.add_molecule(top_mol.reference_molecule)

        self_copy.topology.mdtop = md.Topology.from_openmm(
            self_copy.topology.to_openmm()
        )

        for handler_name, handler in other.handlers.items():
            self_handler = self_copy.handlers[handler_name]
            if handler_name == "Electrostatics":
                # Deal with electrostatics separately
                continue
            for top_key, pot_key in handler.slot_map.items():
                new_atom_indices = tuple(
                    idx + atom_offset for idx in top_key.atom_indices
                )
                new_top_key = TopologyKey(
                    atom_indices=new_atom_indices,
                    mult=top_key.mult,
                )
                self_handler.slot_map.update({new_top_key: pot_key})
                self_handler.potentials.update({pot_key: handler.potentials[pot_key]})

        for atom_key, charge in other["Electrostatics"].charges.items():
            new_atom_indices = (atom_key.atom_indices[0] + atom_offset,)
            new_top_key = TopologyKey(atom_indices=new_atom_indices, mult=atom_key.mult)
            self_copy["Electrostatics"].charges.update({new_top_key: charge})

        new_positions = np.vstack([self_copy.positions, other.positions])
        self_copy.positions = new_positions

        if not np.all(self_copy.box == other.box):
            raise NotImplementedError(
                "Combination with unequal box vectors is not curretnly supported"
            )

        return self_copy

    def __repr__(self):
        periodic = self.box is not None
        try:
            n_atoms = self.topology.mdtop.n_atoms
        except AttributeError:
            n_atoms = "unknown number of"
        except NameError:
            n_atoms = self.topology.n_topology_atoms
        return f"System with {n_atoms} atoms, {'' if periodic else 'non-'}periodic topology"
