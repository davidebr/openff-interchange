from simtk import openmm, unit

from openff.system.exceptions import UnsupportedCutoffMethodError
from openff.system.interop.parmed import _lj_params_from_potential

kcal_mol = unit.kilocalorie_per_mole
kcal_ang = kcal_mol / unit.angstrom ** 2
kcal_rad = kcal_mol / unit.radian ** 2


def to_openmm(openff_sys) -> openmm.System:
    """Convert an OpenFF System to a ParmEd Structure"""

    openmm_sys = openmm.System()

    # OpenFF box stored implicitly as nm, and that happens to be what
    # OpenMM casts box vectors to if provided only an np.ndarray
    if openff_sys.box is not None:
        openmm_sys.setDefaultPeriodicBoxVectors(*openff_sys.box)

    _process_bond_forces(openff_sys, openmm_sys)
    _process_angle_forces(openff_sys, openmm_sys)
    _process_proper_torsion_forces(openff_sys, openmm_sys)
    _process_impproper_torsion_forces(openff_sys, openmm_sys)
    _process_nonbonded_forces(openff_sys, openmm_sys)

    return openmm_sys


def _process_bond_forces(openff_sys, openmm_sys):
    harmonic_bond_force = openmm.HarmonicBondForce()
    openmm_sys.addForce(harmonic_bond_force)

    bond_handler = openff_sys.handlers["Bonds"]
    for bond, key in bond_handler.slot_map.items():
        indices = eval(bond)
        params = bond_handler.potentials[key].parameters
        k = params["k"] * kcal_ang
        length = params["length"] * unit.angstrom

        harmonic_bond_force.addBond(
            particle1=indices[0],
            particle2=indices[1],
            length=length,
            k=k,
        )


def _process_angle_forces(openff_sys, openmm_sys):
    harmonic_angle_force = openmm.HarmonicAngleForce()
    openmm_sys.addForce(harmonic_angle_force)

    angle_handler = openff_sys.handlers["Angles"]
    for angle, key in angle_handler.slot_map.items():
        indices = eval(angle)
        params = angle_handler.potentials[key].parameters
        k = params["k"] * kcal_ang
        angle = params["angle"] * unit.degree

        harmonic_angle_force.addAngle(
            particle1=indices[0],
            particle2=indices[1],
            particle3=indices[2],
            angle=angle,
            k=k,
        )

    pass


def _process_proper_torsion_forces(openff_sys, openmm_sys):
    pass


def _process_impproper_torsion_forces(openff_sys, openmm_sys):
    pass


def _process_nonbonded_forces(openff_sys, openmm_sys):
    # Store the pairings, not just the supported methods for each
    supported_cutoff_methods = [["Cutoff", "PME"]]

    vdw_handler = openff_sys.handlers["vdW"]
    if vdw_handler.method not in [val[0] for val in supported_cutoff_methods]:
        raise UnsupportedCutoffMethodError()

    vdw_cutoff = vdw_handler.cutoff * unit.angstrom

    electrostatics_handler = openff_sys.handlers["Electrostatics"]  # Split this out
    if electrostatics_handler.method not in [v[1] for v in supported_cutoff_methods]:
        raise UnsupportedCutoffMethodError()

    non_bonded_force = openmm.NonbondedForce()
    openmm_sys.addForce(non_bonded_force)

    if vdw_handler.method == "cutoff":
        if openff_sys.box is None:
            non_bonded_force.setNonbondedMethod(openmm.NonbondedForce.NoCutoff)
        else:
            non_bonded_force.setNonbondedMethod(openmm.NonbondedForce.PME)
            non_bonded_force.setUseDispersionCorrection(True)
            non_bonded_force.setCutoffDistance(vdw_cutoff)

    for vdw_atom, vdw_smirks in vdw_handler.slot_map.values():
        atom_idx = eval(vdw_atom)[0]

        partial_charge = electrostatics_handler.charge_map[vdw_atom]
        vdw_potential = vdw_handler.potentials[vdw_smirks]
        sigma, epsilon = _lj_params_from_potential(vdw_potential)

        non_bonded_force.setParticleParameters(atom_idx, partial_charge, sigma, epsilon)
