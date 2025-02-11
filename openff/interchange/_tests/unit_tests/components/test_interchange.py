from copy import deepcopy

import numpy
import pytest
from openff.toolkit.topology import Molecule, Topology
from openff.toolkit.typing.engines.smirnoff.parameters import (
    ElectrostaticsHandler,
    ParameterHandler,
)
from openff.units import unit
from openff.utilities.testing import skip_if_missing

from openff.interchange import Interchange
from openff.interchange._tests import (
    _BaseTest,
    get_test_file_path,
    needs_gmx,
    needs_lmp,
)
from openff.interchange.drivers import get_openmm_energies
from openff.interchange.exceptions import (
    ExperimentalFeatureException,
    InvalidTopologyError,
    MissingParameterHandlerError,
    MissingParametersError,
    MissingPositionsError,
    MissingVirtualSitesError,
    SMIRNOFFHandlersNotImplementedError,
)

try:
    from pydantic.v1 import ValidationError
except ImportError:
    from pydantic import ValidationError


@pytest.mark.slow()
class TestInterchange(_BaseTest):
    def test_getitem(self, sage):
        """Test behavior of Interchange.__getitem__"""
        mol = Molecule.from_smiles("CCO")
        out = Interchange.from_smirnoff(force_field=sage, topology=[mol])

        out.box = [4, 4, 4]

        assert not out.positions
        numpy.testing.assert_equal(out["box"].m, (4 * numpy.eye(3) * unit.nanometer).m)
        numpy.testing.assert_equal(out["box"].m, out["box_vectors"].m)

        assert out["Bonds"] == out.collections["Bonds"]

        with pytest.raises(LookupError, match="Only str"):
            out[1]

        with pytest.raises(LookupError, match="Could not find"):
            out["CMAPs"]

    def test_get_parameters(self, sage):
        mol = Molecule.from_smiles("CCO")
        out = Interchange.from_smirnoff(force_field=sage, topology=[mol])

        from_interchange = out._get_parameters("Bonds", (0, 4))
        from_handler = out["Bonds"]._get_parameters((0, 4))

        assert "k" in from_interchange.keys()
        assert "length" in from_interchange.keys()
        assert from_interchange == from_handler

        with pytest.raises(MissingParameterHandlerError, match="Foobar"):
            out._get_parameters("Foobar", (0, 1))

        with pytest.raises(MissingParametersError, match=r"atoms \(0, 100\)"):
            out._get_parameters("Bonds", (0, 100))

    def test_missing_electrostatics_handler(self, tip3p, water):
        """Test that an error is raised when an electrostatics handler is missing"""
        tip3p.deregister_parameter_handler("Electrostatics")

        topology = water.to_topology()
        topology.box_vectors = unit.Quantity([4, 4, 4], units=unit.nanometer)

        with pytest.raises(MissingParameterHandlerError, match="modify partial"):
            Interchange.from_smirnoff(tip3p, topology)

        tip3p.register_parameter_handler(
            ElectrostaticsHandler(skip_version_check=True),
        )

        Interchange.from_smirnoff(tip3p, topology)

        tip3p["Electrostatics"].cutoff = 7.89 * unit.angstrom

        out = Interchange.from_smirnoff(tip3p, topology)

        assert out["Electrostatics"].cutoff == 7.89 * unit.angstrom

    def test_box_setter(self):
        tmp = Interchange()

        with pytest.raises(ValidationError):
            tmp.box = [2, 2, 3, 90, 90, 90]

    @skip_if_missing("openmm")
    def test_basic_combination(self, monkeypatch, sage_unconstrained):
        """Test basic use of Interchange.__add__() based on the README example"""
        monkeypatch.setenv("INTERCHANGE_EXPERIMENTAL", "1")

        mol = Molecule.from_smiles("C")
        mol.generate_conformers(n_conformers=1)
        top = Topology.from_molecules([mol])

        interchange = Interchange.from_smirnoff(sage_unconstrained, top)

        interchange.box = [4, 4, 4] * numpy.eye(3)
        interchange.positions = mol.conformers[0]

        # Copy and translate atoms by [1, 1, 1]
        other = Interchange()
        other = deepcopy(interchange)
        other.positions += 1.0 * unit.nanometer

        combined = interchange + other

        # Just see if it can be converted into OpenMM and run
        get_openmm_energies(combined)

    def test_parameters_do_not_clash(self, monkeypatch, sage_unconstrained):
        monkeypatch.setenv("INTERCHANGE_EXPERIMENTAL", "1")

        thf = Molecule.from_smiles("C1CCOC1")
        ace = Molecule.from_smiles("CC(=O)O")

        thf.generate_conformers(n_conformers=1)
        ace.generate_conformers(n_conformers=1)

        def make_interchange(molecule: Molecule) -> Interchange:
            molecule.generate_conformers(n_conformers=1)
            interchange = Interchange.from_smirnoff(
                force_field=sage_unconstrained,
                topology=[molecule],
            )
            interchange.positions = molecule.conformers[0]

            return interchange

        thf_interchange = make_interchange(thf)
        ace_interchange = make_interchange(ace)
        complex_interchange = thf_interchange + ace_interchange

        thf_vdw = thf_interchange["vdW"].get_system_parameters()
        ace_vdw = ace_interchange["vdW"].get_system_parameters()
        add_vdw = complex_interchange["vdW"].get_system_parameters()

        numpy.testing.assert_equal(numpy.vstack([thf_vdw, ace_vdw]), add_vdw)

        # TODO: Ensure the de-duplication is maintained after exports

    def test_positions_setting(self, monkeypatch, sage):
        """Test that positions exist on the result if and only if
        both input objects have positions."""

        monkeypatch.setenv("INTERCHANGE_EXPERIMENTAL", "1")

        ethane = Molecule.from_smiles("CC")
        methane = Molecule.from_smiles("C")

        ethane_interchange = Interchange.from_smirnoff(
            sage,
            [ethane],
        )
        methane_interchange = Interchange.from_smirnoff(sage, [methane])

        ethane.generate_conformers(n_conformers=1)
        methane.generate_conformers(n_conformers=1)

        assert (methane_interchange + ethane_interchange).positions is None
        methane_interchange.positions = methane.conformers[0]
        assert (methane_interchange + ethane_interchange).positions is None
        ethane_interchange.positions = ethane.conformers[0]
        assert (methane_interchange + ethane_interchange).positions is not None

    def test_input_topology_not_modified(self, sage):
        molecule = Molecule.from_smiles("CCO")
        molecule.generate_conformers(n_conformers=1)
        molecule.conformers[0] += 1 * unit.angstrom
        topology = molecule.to_topology()
        original = list(topology.molecules)[0].conformers[0]

        Interchange.from_smirnoff(force_field=sage, topology=topology)
        new = list(topology.molecules)[0].conformers[0]

        assert numpy.sum((original - new).m_as(unit.angstrom)) == pytest.approx(0)

    @pytest.mark.skip("LAMMPS export experimental")
    @needs_gmx
    @needs_lmp
    @pytest.mark.slow()
    @skip_if_missing("foyer")
    def test_atom_ordering(self):
        """Test that atom indices in bonds are ordered consistently between the slot map and topology"""
        import foyer

        from openff.interchange import Interchange
        from openff.interchange.drivers import (
            get_gromacs_energies,
            get_lammps_energies,
            get_openmm_energies,
        )

        oplsaa = foyer.forcefields.load_OPLSAA()

        benzene = Molecule.from_file(get_test_file_path("benzene.sdf"))
        benzene.name = "BENZ"
        out = Interchange.from_foyer(force_field=oplsaa, topology=benzene.to_topology())
        out.box = [4, 4, 4]
        out.positions = benzene.conformers[0]

        # Violates OPLS-AA, but the point here is just to make sure everything runs
        out["vdW"].mixing_rule = "lorentz-berthelot"

        get_gromacs_energies(out)
        get_openmm_energies(out)
        get_lammps_energies(out)

    def test_from_sage(self, sage):
        top = Topology.from_molecules(
            [Molecule.from_smiles("CCO"), Molecule.from_smiles("CC")],
        )

        out = Interchange.from_smirnoff(sage, top)

        assert "Constraints" in out.collections.keys()
        assert "Bonds" in out.collections.keys()
        assert "Angles" in out.collections.keys()
        assert "ProperTorsions" in out.collections.keys()
        assert "vdW" in out.collections.keys()

        assert type(out.topology) is Topology
        assert isinstance(out.topology, Topology)

    @skip_if_missing("openmm")
    def test_validate_simple_topology(self, sage):
        from openff.interchange.components.toolkit import _simple_topology_from_openmm

        tmp = Interchange()
        tmp.topology = _simple_topology_from_openmm(
            Molecule.from_smiles("CCO").to_topology().to_openmm(),
        )

    def test_from_sage_molecule_list(self, sage):
        out = Interchange.from_smirnoff(
            sage,
            [Molecule.from_smiles("CCO"), Molecule.from_smiles("CC")],
        )

        assert "Constraints" in out.collections.keys()
        assert "Bonds" in out.collections.keys()
        assert "Angles" in out.collections.keys()
        assert "ProperTorsions" in out.collections.keys()
        assert "vdW" in out.collections.keys()

        assert type(out.topology) is Topology
        assert isinstance(out.topology, Topology)

    @skip_if_missing("openmm")
    def test_to_openmm_simulation(self, sage):
        import numpy
        import openmm
        import openmm.app
        import openmm.unit

        molecule = Molecule.from_smiles("CCO")

        simulation = Interchange.from_smirnoff(
            force_field=sage,
            topology=molecule.to_topology(),
        ).to_openmm_simulation(
            integrator=openmm.VerletIntegrator(2.0 * openmm.unit.femtosecond),
        )

        numpy.testing.assert_allclose(
            numpy.linalg.norm(
                simulation.context.getState(getPositions=True).getPositions(
                    asNumpy=True,
                ),
            ),
            numpy.zeros((molecule.n_atoms, 3)),
        )

        del simulation

        molecule.generate_conformers(n_conformers=1)

        simulation = Interchange.from_smirnoff(
            force_field=sage,
            topology=molecule.to_topology(),
        ).to_openmm_simulation(
            integrator=openmm.VerletIntegrator(2.0 * openmm.unit.femtosecond),
        )

        numpy.testing.assert_allclose(
            simulation.context.getState(getPositions=True).getPositions(asNumpy=True),
            molecule.conformers[0].m_as(unit.nanometer),
        )

    @skip_if_missing("nglview")
    @skip_if_missing("openmm")
    def test_visualize(self, sage):
        import nglview

        molecule = Molecule.from_smiles("CCO")

        out = Interchange.from_smirnoff(
            force_field=sage,
            topology=molecule.to_topology(),
        )

        with pytest.raises(
            MissingPositionsError,
            match="Cannot visualize system without positions.",
        ):
            out.visualize()

        molecule.generate_conformers(n_conformers=1)
        out.positions = molecule.conformers[0]

        assert isinstance(out.visualize(), nglview.NGLWidget)


@skip_if_missing("openmm")
@skip_if_missing("mdtraj")
class TestToPDB(_BaseTest):
    def test_to_pdb_with_virtual_sites(self, water, tip4p):
        import mdtraj

        tip4p.create_interchange(water.to_topology()).to_pdb(
            "_test.pdb",
            include_virtual_sites=True,
        )

        assert mdtraj.load("_test.pdb").topology.n_atoms == 4

    def test_tip4p_pdb_dummy_particle_position(self, water_tip4p, tip4p):
        import mdtraj

        tip4p.create_interchange(water_tip4p.to_topology()).to_pdb(
            "_test.pdb",
            include_virtual_sites=True,
        )

        # openmm.app.PDBFile does not load all 4 particles,
        # despite it being used to write this (?!)
        xyz = mdtraj.load("_test.pdb").xyz[0]

        # https://github.com/pandegroup/tip3p-tip4p-fb/blob/e590f212a4e67d711cbe0aa6b6955ee51ef8b800/AMBER/dat/leap/parm/frcmod.tip4pfb#L7
        numpy.testing.assert_allclose(
            numpy.linalg.norm(xyz[0] - xyz[-1]),
            0.010527,
            atol=0.0005,
        )

    def test_to_pdb_ignoring_virtual_sites(self, water, tip4p):
        import mdtraj

        tip4p.create_interchange(water.to_topology()).to_pdb(
            "_test.pdb",
            include_virtual_sites=False,
        )

        assert mdtraj.load("_test.pdb").topology.n_atoms == 3

    def test_to_pdb_missing_virtual_sites(self, tip3p, water):
        with pytest.raises(
            MissingVirtualSitesError,
        ):
            tip3p.create_interchange(water.to_topology()).to_pdb(
                "_test.pdb",
                include_virtual_sites=True,
            ),


class TestUnimplementedSMIRNOFFCases(_BaseTest):
    def test_bogus_smirnoff_handler(self, sage):
        top = Molecule.from_smiles("CC").to_topology()

        bogus_parameter_handler = ParameterHandler(version=0.3)
        bogus_parameter_handler._TAGNAME = "bogus"
        sage.register_parameter_handler(bogus_parameter_handler)
        with pytest.raises(
            SMIRNOFFHandlersNotImplementedError,
            match="not implemented in Interchange:.*bogus",
        ):
            Interchange.from_smirnoff(force_field=sage, topology=top)


class TestBadExports(_BaseTest):
    @skip_if_missing("openmm")
    def test_invalid_topology(self, sage):
        """Test that InvalidTopologyError is caught when passing an unsupported
        topology type to Interchange.from_smirnoff"""
        top = Molecule.from_smiles("CC").to_topology().to_openmm()

        # In some configurations, Pydantic may pre-emptively raise ValidationError because of the type mismatch
        # with pytest.raises(ValidationError):
        with pytest.raises(
            InvalidTopologyError,
            match="Could not process topology argument.*openmm.*",
        ):
            Interchange.from_smirnoff(force_field=sage, topology=top)

    def test_gro_file_no_positions(self, sage):
        no_positions = Interchange.from_smirnoff(
            force_field=sage,
            topology=[Molecule.from_smiles("CC")],
        )
        with pytest.raises(MissingPositionsError, match="Positions are req"):
            no_positions.to_gro("foo.gro")

    def test_gro_file_all_zero_positions(self, sage):
        zero_positions = Interchange.from_smirnoff(
            force_field=sage,
            topology=[Molecule.from_smiles("CC")],
        )
        zero_positions.positions = unit.Quantity(
            numpy.zeros((zero_positions.topology.n_atoms, 3)),
            unit.nanometer,
        )
        with pytest.warns(UserWarning, match="seem to all be zero"):
            zero_positions.to_gro("foo.gro")


@skip_if_missing("openmm")
class TestInterchangeSerialization(_BaseTest):
    def test_json_roundtrip(self, sage, water, ethanol):
        topology = Topology.from_molecules(
            [
                water,
                water,
            ],
        )

        for molecule in topology.molecules:
            molecule.generate_conformers(n_conformers=1)

        topology.box_vectors = unit.Quantity([4, 4, 4], unit.nanometer)

        original = Interchange.from_smirnoff(
            force_field=sage,
            topology=topology,
        )

        roundtripped = Interchange.parse_raw(original.json())

        get_openmm_energies(original, combine_nonbonded_forces=False).compare(
            get_openmm_energies(roundtripped, combine_nonbonded_forces=False),
        )


class TestWrappedCalls(_BaseTest):
    """Test that methods which delegate out to other submodules call them."""

    @pytest.fixture()
    def simple_interchange(self, sage):
        mol = Molecule.from_smiles("CCO")
        mol.generate_conformers(n_conformers=1)
        top = mol.to_topology()
        top.box_vectors = unit.Quantity(numpy.eye(3) * 4, unit.nanometer)

        return Interchange.from_smirnoff(force_field=sage, topology=top)

    @skip_if_missing("openmm")
    def test_from_openmm_error(self):
        with pytest.raises(ExperimentalFeatureException):
            Interchange.from_openmm()

    def test_from_gromacs_error(self):
        with pytest.raises(ExperimentalFeatureException):
            Interchange.from_gromacs()

    @skip_if_missing("openmm")
    @pytest.mark.slow()
    def test_from_openmm_called(self, monkeypatch, simple_interchange):
        monkeypatch.setenv("INTERCHANGE_EXPERIMENTAL", "1")

        topology = simple_interchange.to_openmm_topology()
        system = simple_interchange.to_openmm()
        positions = simple_interchange.positions
        box = simple_interchange.box

        Interchange.from_openmm(
            topology=topology,
            system=system,
            positions=positions,
            box_vectors=box,
        )

    def test_from_gromacs_called(self, monkeypatch, simple_interchange):
        monkeypatch.setenv("INTERCHANGE_EXPERIMENTAL", "1")

        simple_interchange.to_gromacs(prefix="tmp_")

        Interchange.from_gromacs(
            topology_file="tmp_.top",
            gro_file="tmp_.gro",
        )
