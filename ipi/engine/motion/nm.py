"""Contains the classes that deal with the different dynamics required in
different types of ensembles.

Copyright (C) 2013, Joshua More and Michele Ceriotti

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program. If not, see <http.//www.gnu.org/licenses/>.
"""

__all__ = ['NormalModeMover']

import numpy as np
import time
import itertools
import os

from ipi.engine.motion import Motion
from ipi.utils.depend import *
from ipi.utils import units
from ipi.utils.softexit import softexit
from ipi.utils.messages import verbosity, warning, info
from ipi.utils.io import print_file
from ipi.engine.atoms import Atoms
from scipy.special import hermite
from scipy.interpolate import interp1d
from scipy.interpolate import interp2d
from scipy.interpolate import RegularGridInterpolator
from scipy.misc import logsumexp
import time
#from guppy import hpy

#==========================================================================
#==========================================================================


class NormalModeMover(Motion):
    """Normal Mode analysis.
    """

    def __init__(self, fixcom=False, fixatoms=None, mode="imf", dynmat=np.zeros(0, float),  prefix="", asr="none", nprim="1", fnmrms="1.0", nevib="25.0", nint="101", nbasis="10", athresh="1e-2", ethresh="1e-2", nkbt="4.0", nexc="5", mptwo=False, print_mftpot=False, print_2b_map=False, threebody=False, print_vib_density=False, nparallel=1):
        """Initialises NormalModeMover.
        Args:
        fixcom	: An optional boolean which decides whether the centre of mass
                  motion will be constrained or not. Defaults to False. 
        dynmatrix : A 3Nx3N array that stores the dynamic matrix.
        refdynmatrix : A 3Nx3N array that stores the refined dynamic matrix.
        """

        super(NormalModeMover, self).__init__(fixcom=fixcom, fixatoms=fixatoms)

        # Finite difference option.
        self.mode = mode
        if self.mode == "imf":
            self.calc = IMF()
        elif self.mode == "pc":
            self.calc = PC()
        elif self.mode == "vscfmapper":
            self.calc = VSCFMapper()
        elif self.mode == "vscfsolver":
            self.calc = VSCFSolver()

        self.dynmatrix = dynmat
        self.mode = mode
        self.frefine = False
        self.U = None
        self.V = None
        self.prefix = prefix
        self.asr = asr
        self.nprim = nprim #1
        self.nz = 0 #1
        self.fnmrms = fnmrms #1.0
        self.nevib = nevib #25.0
        self.nint = nint #101
        self.nbasis = nbasis #10
        self.athresh = athresh #1e-2
        self.ethresh = ethresh #1e-2
        self.nkbt = nkbt #4.0
        self.nexc = nexc #5
        self.mptwo = mptwo #False
        self.print_mftpot = print_mftpot #False
        self.print_2b_map = print_2b_map #False
        self.threebody = threebody #False
        self.print_vib_density = print_vib_density #False
        self.nparallel = nparallel #False

        if self.prefix == "":
            self.prefix = "nm"

    def bind(self, ens, beads, nm, cell, bforce, prng, omaker):

        super(NormalModeMover, self).bind(ens, beads, nm, cell, bforce, prng, omaker)
        self.temp = self.ensemble.temp

        # Raises error for nbeads not equal to 1.
        if(self.beads.nbeads > 1):
            raise ValueError("Calculation not possible for number of beads greater than one.")

        self.ism = 1 / np.sqrt(dstrip(self.beads.m3[-1]))
        self.m = dstrip(self.beads.m)
        self.calc.bind(self)

        self.dbeads = self.beads.copy(nbeads=self.nparallel)
        self.dcell = self.cell.copy()
        self.dforces = self.forces.copy(self.dbeads, self.dcell)

    def step(self, step=None):
        """Executes one step of phonon computation. """
        if (step < 3 * self.beads.natoms):
            self.calc.step(step)
        else:
            self.calc.terminate()
            softexit.trigger("Independent Mode Calculation has terminated. Exiting.")

    def apply_asr(self, dm):
        """
        Removes the translations and/or rotations depending on the asr mode.
        """
        if(self.asr == "none"):
            return dm

        if(self.asr == "crystal"):
            # Computes the centre of mass.
            com = np.dot(np.transpose(self.beads.q.reshape((self.beads.natoms, 3))), self.m) / self.m.sum()
            qminuscom = self.beads.q.reshape((self.beads.natoms, 3)) - com
            # Computes the moment of inertia tensor.
            moi = np.zeros((3, 3), float)
            for k in range(self.beads.natoms):
                moi -= np.dot(np.cross(qminuscom[k], np.identity(3)), np.cross(qminuscom[k], np.identity(3))) * self.m[k]

            U = (np.linalg.eig(moi))[1]
            R = np.dot(qminuscom, U)
            D = np.zeros((3, 3 * self.beads.natoms), float)

            # Computes the vectors along rotations.
            D[0] = np.tile([1, 0, 0], self.beads.natoms) / self.ism
            D[1] = np.tile([0, 1, 0], self.beads.natoms) / self.ism
            D[2] = np.tile([0, 0, 1], self.beads.natoms) / self.ism

            # Computes unit vecs.
            for k in range(3):
                D[k] = D[k] / np.linalg.norm(D[k])

            # Computes the transformation matrix.
            transfmatrix = np.eye(3 * self.beads.natoms) - np.dot(D.T, D)
            r = np.dot(transfmatrix.T, np.dot(dm, transfmatrix))

            # Sets the number of  zero modes.
            self.nz = 3
            return r

        elif(self.asr == "poly"):
            # Computes the centre of mass.
            com = np.dot(np.transpose(self.beads.q.reshape((self.beads.natoms, 3))), self.m) / self.m.sum()
            qminuscom = self.beads.q.reshape((self.beads.natoms, 3)) - com
            # Computes the moment of inertia tensor.
            moi = np.zeros((3, 3), float)
            for k in range(self.beads.natoms):
                moi -= np.dot(np.cross(qminuscom[k], np.identity(3)), np.cross(qminuscom[k], np.identity(3))) * self.m[k]

            U = (np.linalg.eig(moi))[1]
            R = np.dot(qminuscom, U)
            D = np.zeros((6, 3 * self.beads.natoms), float)

            # Computes the vectors along translations and rotations.
            D[0] = np.tile([1, 0, 0], self.beads.natoms) / self.ism
            D[1] = np.tile([0, 1, 0], self.beads.natoms) / self.ism
            D[2] = np.tile([0, 0, 1], self.beads.natoms) / self.ism
            for i in range(3 * self.beads.natoms):
                iatom = i / 3
                idof = np.mod(i, 3)
                D[3, i] = (R[iatom, 1] * U[idof, 2] - R[iatom, 2] * U[idof, 1]) / self.ism[i]
                D[4, i] = (R[iatom, 2] * U[idof, 0] - R[iatom, 0] * U[idof, 2]) / self.ism[i]
                D[5, i] = (R[iatom, 0] * U[idof, 1] - R[iatom, 1] * U[idof, 0]) / self.ism[i]

            # Computes unit vecs.
            for k in range(6):
                D[k] = D[k] / np.linalg.norm(D[k])

            # Computes the transformation matrix.
            transfmatrix = np.eye(3 * self.beads.natoms) - np.dot(D.T, D)
            r = np.dot(transfmatrix.T, np.dot(dm, transfmatrix))

            # Sets the number of zero modes. (Assumes poly-atomic molecules)
            self.nz = 6
            return r


#==========================================================================
#==========================================================================


class DummyCalculator(dobject):
    """ No-op Calculator """

    def __init__(self):
        pass

    def bind(self, imm):
        """ Reference all the variables for simpler access."""
        self.imm = imm

    def step(self, step=None):
        """Dummy simulation time step which does nothing."""
        pass

    def terminate(self):
        """Dummy transformation step which does nothing."""
        pass


#==========================================================================
#==========================================================================


class IMF(DummyCalculator):
    """ Temperature scaled normal mode Born-Oppenheimer surface evaluator.
    """

    def bind(self, imm):
        """ Reference all the variables for simpler access."""
        super(IMF, self).bind(imm)

        # Initialises a 3*number of atoms X 3*number of atoms dynamic matrix.
        if(self.imm.dynmatrix.size != (self.imm.beads.q.size * self.imm.beads.q.size)):
            if(self.imm.dynmatrix.size == 0):
                self.imm.dynmatrix = np.zeros((self.imm.beads.q.size, self.imm.beads.q.size), float)
            else:
                raise ValueError("Force constant matrix size does not match system size")
        else:
            self.imm.dynmatrix = self.imm.dynmatrix.reshape(((self.imm.beads.q.size, self.imm.beads.q.size)))

        # Applies ASR
        self.imm.dynmatrix = self.imm.apply_asr(self.imm.dynmatrix)

        # Calculates normal mode directions.
        self.imm.w2, self.imm.U = np.linalg.eigh(self.imm.dynmatrix)

        # Calculates the normal mode frequencies.
        # TODO : Should also handle soft modes.
        self.imm.w = self.imm.w2 * 0
        self.imm.w[self.imm.nz:] = np.sqrt(dstrip(self.imm.w2[self.imm.nz:]))
        
        self.imm.V = self.imm.U.copy()
        for i in xrange(len(self.imm.V)):
            self.imm.V[:, i] *= self.imm.ism

        # Harm ZP RMS displacement along normal mode
        # Not temperature dependent so that sampled potentials can easily be 
        # reused to evaluate free energy at different temperature.
        self.imm.nmrms = np.zeros(len(self.imm.w)) 
        self.imm.nmrms[self.imm.nz:] = np.sqrt( 0.5 / self.imm.w[self.imm.nz:])

        # Harm vibr energy at finite temp
        # Similarly this is also not temperature dependent.
        self.imm.nmevib =  0.5 * self.imm.w 

        # Fraction of the harmonic RMS displacement 
        # used to sample along a normal mode
        self.fnmrms = self.imm.fnmrms

        # Multiple of harmonic vibrational energy up to which 
        # the BO surface is sampled 
        self.nevib = self.imm.nevib

        # Integration points for numerical integration of 
        # Hamiltonian matrix elements 
        self.nint = self.imm.nint

        # Number of SHO states used as basis for anharmonic wvfn
        self.nbasis = self.imm.nbasis

        # Convergence threshold for fractional error in vibrational free energy
        self.athresh = self.imm.athresh

        # Number of primitive unit (cells) per simulation (cell) 
        self.nprim = self.imm.nprim

        # Total number of vibrational modes
        self.dof = 3 * self.imm.beads.natoms 

        # Initializes the (an)har energy correction
        self.total_anhar_free_energy = 0.0
        self.total_har_free_energy = 0.0
        self.total_anhar_internal_energy = 0.0
        self.total_har_internal_energy = 0.0

        # Potential energy (offset) at equilibrium positions per primitve unit (cell)
        self.v0 = 0

        # Initializes the SHO wvfn basis for solving the 1D Schroedinger equation
        self.hermite_functions = [hermite(i) for i in xrange(max(20, 4 * self.nbasis))]

        # Evaluates the wave functions on a grid

    def psi(self, n, m, hw, q):
        """
        Returns the value of the n^th wavefunction of a 
        harmonic oscillator with mass m, frequency freq
        at position x.
        """

        # Defines variables for easier referencing
        alpha = m * hw
        try:
          herfun = self.hermite_functions[n]
        except:
          herfun = hermite(n)

        norm = (alpha / np.pi)**0.25 * np.sqrt( 1.0 / (2.0 ** (n) * np.math.factorial(n)))
        psival = norm * np.exp(-alpha * q**2 / 2.0) * herfun(np.sqrt(alpha) * q)

        return psival

    def solve_schroedingers_equation(self, hw, nbasis, qlist, vspline):
        """
        Given nbasis the number of elements in the basis set
        and potential the potential energy on a grid, solves
        the schrodinger's equation.
        """

        # Set up the wavefunction basis
        psigrid = np.zeros((self.nint, nbasis))
        norm_psi = np.zeros(nbasis)
        mass = 1

        # Defines the grid in normal mode position space.
        dnmd = np.linspace(np.min(qlist), np.max(qlist), self.nint)

        # Calculates the wavefunctions on the grid.
        norm = np.zeros(nbasis)
        for i in xrange(nbasis):
            psigrid[:,i] = self.psi(i, mass, hw, dnmd)
            norm[i] = np.dot(psigrid.T[i],psigrid.T[i])

        # Construct the Hamiltonian matrix.
        h = np.zeros((nbasis,nbasis))
        vsplinegrid = np.asarray([(np.nan_to_num(vspline(x))) for x in dnmd])

        for i in xrange(nbasis):
            for j in xrange(i, nbasis, 1):
                h[i][j] = np.dot(psigrid[:,i] * psigrid[:,j], vsplinegrid) / np.sqrt(norm[i] * norm[j])
            h[i][i] *= 0.5
            h[i][i] += 0.5 * (i + 0.5) * hw
        h += h.T 

        # Diagonalise Hamiltonian matrix and evaluate anharmonic free energy and vibrational freq
        evals, evecs = np.linalg.eigh(h)
        
        # Calculates the free and internal energy
        A = -logsumexp(-1.0 * evals / dstrip(self.imm.temp)) * dstrip(self.imm.temp)
        E = np.sum(evals * np.exp(-1.0 * evals / dstrip(self.imm.temp))) / np.sum(np.exp(-1.0 * evals / dstrip(self.imm.temp)))
        return A, E

        

    def step(self, step=None):
        """Computes the Born Oppenheimer curve along a normal mode."""

        # Ignores (near) zero modes.
        if step < self.imm.nz:
            info(" @NM : Ignoring the zero mode.", verbosity.medium)
            info(" ", verbosity.medium)
            return 
        elif self.imm.w[step] < 9.1126705e-06:
            info(" @NM : Ignoring normal mode no.  %8d with frequency %15.8f cm^-1." % (step, self.imm.w[step] * 219474) , verbosity.medium)
            info(" ", verbosity.medium)
            self.imm.nz += 1
            return
        else:
            info(" @NM : Treating normal mode no.  %8d with frequency %15.8f cm^-1." % (step, self.imm.w[step] * 219474) ,verbosity.medium) 

        # initializes the list containing sampled displacements,
        # forces and energies.
        vlist = []
        flist = []
        qlist = []

        # Adds to the list of "sampled configuration" the one that
        # corresponds to the minimum.
        q0 = 0.0
        v0 = dstrip(self.imm.forces.pots).copy()[0] / self.nprim
        f0 = np.dot(dstrip(self.imm.forces.f).copy()[0], np.real(self.imm.V.T[step])) / self.nprim

        self.v0 = v0 # TODO CHECK IF NECESSARY

        vlist.append(0.0)
        flist.append(f0)
        qlist.append(q0)

        # Converge anharmonic vibrational energies w.r.t. density of sampling points
        Ahar = -logsumexp( [ -1.0 * np.sqrt(self.imm.w2[step]) * (0.5+i) / dstrip(self.imm.temp) for i in range(self.nbasis) ] ) * dstrip(self.imm.temp)
        Zhar =  np.sum([np.exp(-1.0 * np.sqrt(self.imm.w2[step]) * (0.5+i) / dstrip(self.imm.temp)) for i in range(self.nbasis)])
        Ehar =  np.sum([np.sqrt(self.imm.w2[step]) * (0.5+i) * np.exp(  -1.0 * np.sqrt(self.imm.w2[step]) * (0.5+i) / dstrip(self.imm.temp)) for i in range(self.nbasis)]) / Zhar
        Aanh = []
        Eanh = []
        Adiff = []
        Aanh.append(1e-20)
        Adiff.append(0.0)
        Athresh = self.athresh 

        sampling_density_iter = -1

        while True:
            
            sampling_density_iter += 1

            # Doubles the grid spacing, so that an estimate of the
            # anharmonic free energy convergence is 
            # possible at default/input grid spacing
            ffnmrms = self.fnmrms * 0.5**sampling_density_iter * 2.0

            # Calculates the displacement in Cartesian coordinates.
            nmd = ffnmrms * self.imm.nmrms[step]
            dev = np.real(self.imm.V.T[step]) * nmd * np.sqrt(self.nprim)
 
            # After the first iteration doubles the displacement to avoid
            # calculation of the potential at configurations already visited
            # in the previous iteration.
            if (sampling_density_iter == 0):
                delta_counter = 1
            else:
                delta_counter = 2

            counter = 1

            # Explores configurations until the sampled energy exceeds
            # a user-defined threshold of the zero-point energy.
            while True:
                
                # Displaces along the normal mode.
                self.imm.dbeads.q = self.imm.beads.q + dev * counter

                # Stores the "anharmonic" component of the potential
                # and the force.
                dv = dstrip(self.imm.dforces.pots).copy()[0] / self.nprim - 0.50 * self.imm.w2[step] * (nmd * counter)**2 - v0
                df = np.dot(dstrip(self.imm.dforces.f).copy()[0], np.real(self.imm.V.T[step])) / self.nprim + self.imm.w2[step] * (nmd * counter)
   
                # Adds to the list.
                # Also stores the total energetics i.e. including 
                # the harmonic component.
                vlist.append(dv)
                flist.append(df)
                qlist.append(nmd * counter)  
    
                # Bailout condition.
                if self.nevib * self.imm.nmevib[step] < np.abs(0.50 * self.imm.w2[step] * (nmd * counter)**2 + dv):
                    break
                
                # Increases the displacement by 1 or 2 depending on the iteration.
                counter += delta_counter
    
            info(" @NM : Using %8d configurations along the +ve direction." % (counter,), verbosity.medium)

            counter = -1

            # Similarly displaces in the "-ve direction"
            while True:

                # Displaces along the normal mode.
                self.imm.dbeads.q = self.imm.beads.q + dev * counter

                # Stores the "anharmonic" component of the potential
                # and the force.
                dv = dstrip(self.imm.dforces.pots).copy()[0] / self.nprim - 0.50 * self.imm.w2[step] * (nmd * counter)**2 - v0
                df = np.dot(dstrip(self.imm.dforces.f).copy()[0], np.real(self.imm.V.T[step])) / self.nprim + self.imm.w2[step] * (nmd * counter)

                # Adds to the list.
                # Also stores the total energetics i.e. including 
                # the harmonic component.
                vlist.append(dv)
                flist.append(df)
                qlist.append(nmd * counter)  
    
                # Bailout condition.
                if self.nevib * self.imm.nmevib[step] < np.abs(0.50 * self.imm.w2[step] * (nmd * counter)**2 + dv):
                    break

                # Increases the displacement by 1 or 2 depending on the iteration.
                counter -= delta_counter
    
            info(" @NM : Using %8d configurations along the -ve direction." % (-counter,), verbosity.medium)
   
            # Fits cubic splines to data. 
            info("@NM : Fitting cubic splines.", verbosity.high)
            vspline = interp1d(qlist, vlist, kind='cubic', bounds_error=False)

            # Converge wrt size of SHO basis
            bs_iter = 0
            bs_Aanh = [1e-20]
            bs_Eanh = [1e-20]
            
            while True:
                nnbasis = max(1, self.nbasis - 5) + 5 * bs_iter

                # Solves the Schroedinger's Equation.
                bs_AEanh = self.solve_schroedingers_equation(self.imm.w[step], nnbasis, qlist, vspline)
                bs_Aanh.append(bs_AEanh[0])
                bs_Eanh.append(bs_AEanh[1])

                Adiff.append(Aanh-Ahar)
                info(" @NM : CONVERGENCE : fnmrms = %10.8e   nbasis = %5d    A =  %10.8e   D(A) =  %10.8e /  %10.8e" % (ffnmrms, nnbasis, bs_Aanh[-1], np.abs(bs_Aanh[-1] - bs_Aanh[-2]) / np.abs(bs_Aanh[-2]), self.athresh), verbosity.medium)

                # Check whether anharmonic frequency is converged
                if (np.abs(bs_Aanh[-1] - bs_Aanh[-2]) / np.abs(bs_Aanh[-2])) < self.athresh:
                    break

                bs_iter += 1

            Aanh.append(bs_Aanh[-1])
            Eanh.append(bs_Eanh[-1])
            #if sampling_density_iter > 0:
                #info("@ NM : Converging the free energy w.r.t. sampling density size", verbosity.medium)
                #info("fnmrms = %10.8e     A =  %10.8e     D(A) =  %10.8e /  %10.8e" % (ffnmrms, Aanh[-1], np.abs(Aanh[-1] - Aanh[-2]) / np.abs(Aanh[-2]), self.athresh), verbosity.medium)

            # Check whether anharmonic frequency is converged
            if (np.abs(Aanh[-1] - Aanh[-2]) / np.abs(Aanh[-2])) < self.athresh:
                break

        # Prints the normal mode displacement, the potential and the force.
        outfile = self.imm.output_maker.get_output(self.imm.prefix + '.' + str(step) + '.qvf')
        np.savetxt(outfile,  np.c_[qlist, vlist, flist], header="Frequency = %10.8f" % self.imm.w[step])
        outfile.close()

        # prints the mapped potential.
        output_grid = np.linspace(np.min(qlist), np.max(qlist), 100)
        outfile = self.imm.output_maker.get_output(self.imm.prefix + '.' + str(step) + '.vfit')
        np.savetxt(outfile,  np.c_[output_grid, vspline(output_grid)], header="Frequency = %10.8f" % self.imm.w[step])
        outfile.close()

        # Done converging wrt size of SHO basis

        info(' @NM : HAR rmsd          =  %10.8e' % (nmd,), verbosity.medium)
        info(' @NM : HAR frequency     =  %10.8e' % (self.imm.w[step],), verbosity.medium)
        info(' @NM : HAR free energy   =  %10.8e' % (Ahar,), verbosity.medium)
        info(' @NM : IMF free energy   =  %10.8e' % (Aanh[-1],), verbosity.medium)
        info('\n', verbosity.medium )
        self.total_anhar_free_energy += Aanh[-1]
        self.total_har_free_energy += Ahar
        self.total_anhar_internal_energy += Eanh[-1]
        self.total_har_internal_energy += Ehar

    def terminate(self):
        """ Does nothing """
        info(' @NM : Potential offset               =  %10.8e' % (self.v0,), verbosity.medium)
        info(' @NM : HAR free energy                =  %10.8e' % (np.sum((0.5 * np.sqrt(self.imm.w2[self.imm.nz:]) + self.imm.temp * np.log(1.0 - np.exp(-np.sqrt(self.imm.w2[self.imm.nz:]) / self.imm.temp)))) / self.nprim + self.v0,), verbosity.medium)
        info(' @NM : IMF free energy correction     =  %10.8e' % ((self.total_anhar_free_energy - self.total_har_free_energy) / self.nprim,), verbosity.medium)
        info(' @NM : HAR internal energy            =  %10.8e' % (np.sum(np.sqrt(self.imm.w2[self.imm.nz:]) * (0.5 + 1.0 / (np.exp(np.sqrt(self.imm.w2[self.imm.nz:]) / self.imm.temp) -1))) / self.nprim + self.v0,), verbosity.medium)
        info(' @NM : IMF internal energy correction =  %10.8e' % ((self.total_anhar_internal_energy -self.total_har_internal_energy) / self.nprim,), verbosity.medium)
        info(' @NM : ALL QUANTITIES PER PRIMITIVE UNIT CELL (WHERE APPLICABLE) \n', verbosity.medium)


class PC(IMF):
    """ Temperature scaled normal mode Born-Oppenheimer preconditioner for Self Consistent Phonons.
    """

    def bind(self, imm):
        """ Reference all the variables for simpler access."""
        super(PC, self).bind(imm)

        # Initiaizes the preconditioned posititon and frequencies.
        self.qpc = np.zeros(self.imm.beads.q.shape)
        self.w2pc = np.zeros(self.imm.w2.shape)
        self.fnmrms = 2.0 # 4.0 # fraction of the harmonic RMS displacement used to sample along a normal mode

    def step(self, step=None):
        """Computes the Born Oppenheimer curve along a normal mode."""

        print "TREATING NM #", step, self.imm.w2[step]

        if np.abs(self.imm.w2[step]) < 1e-10 :
	   
           print "# IGNORING THE NM. FREQUENCY IS SMALLER THEN 2 cm^-1"
           return 

        # initializes the list containing sampled displacements, forces, and energies
        vlist = []
        flist = []
        qlist = []

        q0 = 0.0
        v0 = dstrip(self.imm.forces.pots).copy()[0] / self.nprim
        f0 = np.dot(dstrip(self.imm.forces.f).copy()[0], np.real(self.imm.V.T[step])) / self.nprim

        self.v0 = v0

        vlist.append(0.0)
        flist.append(f0)
        qlist.append(q0)

        ffnmrms = self.fnmrms
        nmd = ffnmrms * self.imm.nmrms[step]
        dev = np.real(self.imm.V.T[step]) * nmd * np.sqrt(self.nprim)

        counter = 1
        while True:
        # displaces by dev along kth normal mode.
            self.imm.dbeads.q = self.imm.beads.q + dev * counter
            dv = dstrip(self.imm.dforces.pots).copy()[0] / self.nprim - v0
            df = np.dot(dstrip(self.imm.dforces.f).copy()[0], np.real(self.imm.V.T[step])) / self.nprim

            if dv + 0.50 * self.imm.w2[step] * (nmd * counter)**2 >  self.imm.nmevib[step] * ffnmrms * 0.75: 
                vlist.append(dv)
                flist.append(df)
                qlist.append(nmd * counter)  
                break
            counter += 1.0

        print "# NUMBER OF FORCE EVALUATIONS ALONG THE -VE DIRECTION:", counter
        counter = -1
        while True:
        # displaces by dev along kth normal mode.
            self.imm.dbeads.q = self.imm.beads.q + dev * counter
            dv = dstrip(self.imm.dforces.pots).copy()[0] / self.nprim - v0
            df = np.dot(dstrip(self.imm.dforces.f).copy()[0], np.real(self.imm.V.T[step])) / self.nprim

            if dv + 0.50 * self.imm.w2[step] * (nmd * counter)**2 >  self.imm.nmevib[step] * ffnmrms * 0.75: 
                vlist.append(dv)
                flist.append(df)
                qlist.append(nmd * counter)  
                break
            counter -= 1.0 

        print "# NUMBER OF FORCE EVALUATIONS ALONG THE +VE DIRECTION:", counter
        print "# PRINTING"

        np.savetxt(self.imm.prefix + '.' + str(step) + '.v', np.asarray(vlist))
        np.savetxt(self.imm.prefix + '.' + str(step) + '.f', np.asarray(flist))
        np.savetxt(self.imm.prefix + '.' + str(step) + '.q', np.asarray(qlist))

        print "# FITS A QUADRATIC TO THE DATA"
        a,b,c = np.polyfit(x = qlist, y = vlist, deg = 2)
        print a, b, c
        print np.real(self.imm.V.T[step]) * (-b / a / 2.0)  * np.sqrt(self.nprim)
        self.qpc +=  np.real(self.imm.V.T[step]) * (-b / a / 2.0)  * np.sqrt(self.nprim)
        self.w2pc[step] = 2 * a


    def transform(self):
        """ Does nothing """
        self.imm.dbeads.q = self.imm.beads.q + self.qpc
        print 'PRECONDITIONED POSITION                = ', self.imm.dbeads.q 
        print 'PRECONDITIONED FREQUENCIES             = ', self.w2pc
  
        print "PRINTING XYZ FILE"
        dapc = Atoms(self.imm.dbeads.natoms)
        dapc.q = self.imm.dbeads.q[-1]
        dapc.names = self.imm.dbeads.names
        ff = open(self.imm.prefix +  ".qpc." + "xyz", 'w')
        print_file("xyz", dapc, self.imm.cell, filedesc=ff)
        ff.close()

        print "PRINTING THE UPDATED DYNMAT"
        ddmpc = np.eye(self.dof) * 0.0

        for i in range(self.dof):
            U = self.imm.U.T[i].reshape((1, self.dof))
            ddmpc += self.w2pc[i] * np.dot(U.T, U)
        np.savetxt(self.imm.prefix +  ".dmpc." + "data", ddmpc)
#==========================================================================
#==========================================================================

class VSCFMapper(IMF):
    """ 
    """

    def bind(self, imm):
        """ Reference all the variables for simpler access."""
        super(VSCFMapper, self).bind(imm)

        self.print_2b_map = self.imm.print_2b_map
        self.threebody = self.imm.threebody

    def step(self, step=None):
        """Computes the Born Oppenheimer curve along a normal mode."""

        #hpyobject = hpy()

        # soft exit if more than one step, because everything is calculated in one single step
        if step > 0:
            softexit.trigger("VSCF has finished in first (previous) step. Exiting.")

        # Initialize overall potential offset
        v0 = dstrip(self.imm.forces.pots).copy()[0] / self.nprim
        np.savetxt('potoffset.dat',[v0])

        ## IDENTIFY TRANSLATIONS/ROTATIONS
        print "# INITIAL NUMBER OF MODES ",self.dof
        inms = []
        for inm in range(self.dof):
            # skip mode if frequency indicates a translation/rotation
            if np.abs(self.imm.w2[inm]) > 1e-10:
                print "# INCLUDING NM",inm,". FREQUENCY IS LARGER THEN 2 cm^-1"
                inms.append(inm)
        dof = len(inms)
        # save for use in VSCFSolver
        np.savetxt('modes.dat',inms, fmt='%i')

        ## DETERMINE SAMPLING RANGE FOR EACH NORMAL MODE AND SAVE 1D SLICES TO AVOID REDUNDANT REMAPPING IN 2D SLICES
        npts = np.zeros(self.dof, dtype=int)
        nptsmin = np.zeros(self.dof, dtype=int)
        nptsmax = np.zeros(self.dof, dtype=int)
        nptsmaxfile = 'nptsmax.dat'
        nptsminfile = 'nptsmin.dat'
        visfile = 'vindeps.dat'
        vis = []
        if os.path.exists(nptsmaxfile):
            nptsmin = np.loadtxt(nptsminfile)
            nptsmax = np.loadtxt(nptsmaxfile)
            npts[inms] = nptsmin[inms] + nptsmax[inms] + 1
            for inm in inms:
                vi = np.loadtxt('vindeps.'+str(inm)+'.dat')
                vis.append(vi)

        # if mapping has NOT been perfomed previously, actually map full 2D surface
        else:
            for inm in inms:
    
                # determine sampling range for given normal mode
                # eae f0 = np.dot(dstrip(self.imm.forces.f).copy()[0], np.real(self.imm.V.T[inm]))
                nmd = self.fnmrms * self.imm.nmrms[inm]
                dev = np.real(self.imm.V.T[inm]) * nmd * np.sqrt(self.nprim)
                vi = []
                vi.append(v0)
                counter = -1
                while True:
                    self.imm.dbeads.q = self.imm.beads.q + dev * counter
                    dv = dstrip(self.imm.dforces.pots).copy()[0] / self.nprim - v0
                    vi.append(v0 + dv)
                    if self.nevib * self.imm.nmevib[inm] < np.abs(dv):
                        # add two extra points required later for solid spline fitting at edges
                        self.imm.dbeads.q -= dev
                        vi.append(dstrip(self.imm.dforces.pots).copy()[0] / self.nprim)
                        self.imm.dbeads.q -= dev
                        vi.append(dstrip(self.imm.dforces.pots).copy()[0] / self.nprim)
                        break
                    counter -= 1
                print "# NUMBER OF FORCE EVALUATIONS ALONG THE -VE DIRECTION:", counter
                nptsmin[inm] = -counter
                # invert vi so it starts with the potential for the most negative displacement and ends on the equilibrium
                vi = vi[::-1]
                counter = 1
                while True:
                    self.imm.dbeads.q = self.imm.beads.q + dev * counter
                    dv = dstrip(self.imm.dforces.pots).copy()[0] / self.nprim - v0
                    vi.append(v0 + dv)
                    if self.nevib * self.imm.nmevib[inm] < np.abs(dv):
                        # add two extra points required later for solid spline fitting at edges
                        self.imm.dbeads.q += dev
                        vi.append(dstrip(self.imm.dforces.pots).copy()[0] / self.nprim)
                        self.imm.dbeads.q += dev
                        vi.append(dstrip(self.imm.dforces.pots).copy()[0] / self.nprim)
                        break
                    counter += 1
                print "# NUMBER OF FORCE EVALUATIONS ALONG THE +VE DIRECTION:", counter
                nptsmax[inm] = counter
                npts[inm] = nptsmin[inm] + nptsmax[inm] + 1
                # append current 1D slice to array of 1D slices for further use in mapping of 2D slices
                vis.append(vi)
                np.savetxt('vindeps.'+str(inm)+'.dat',vi)
            # save for use in VSCFSolver
            np.savetxt('nptsmin.dat',nptsmin, fmt='%i')
            np.savetxt('nptsmax.dat',nptsmax, fmt='%i')

            # By replacing the previous paragraph with the bemedium one can enforce sampling of a regular symmetric 
            # grid of points. This almediums the output of the mapping to be fed in B. Monserrat implementation of IMF/VSCF.
#                f0 = np.dot(dstrip(self.imm.forces.f).copy()[0], np.real(self.imm.V.T[inm]))
#                nmd = self.fnmrms * self.imm.nmrms[inm]
#                dev = np.real(self.imm.V.T[inm]) * nmd * np.sqrt(self.nprim)
#                vi = []
#                qi = []
#                vi.append(v0)
#                qi.append(0.0)
#                counter = -1
#                for i in range(8):
#                    self.imm.dbeads.q = self.imm.beads.q + dev * counter
#                    qi.append(nmd * counter)
#                    dv = dstrip(self.imm.dforces.pots).copy()[0] / self.nprim - 0.50 * self.imm.w2[inm] * (nmd * counter)**2 - v0
#                    vi.append(v0 + 0.50 * self.imm.w2[inm] * (nmd * counter)**2 + dv)
#                    counter -= 1
#                self.imm.dbeads.q -= dev
#                vi.append(dstrip(self.imm.dforces.pots).copy()[0] / self.nprim)
#                self.imm.dbeads.q -= dev
#                vi.append(dstrip(self.imm.dforces.pots).copy()[0] / self.nprim)
#
#                nptsmin[inm] = -counter -1
#                vi = vi[::-1]
#                qi = qi[::-1]
#                counter = 1
#                for i in range(8):
#                    self.imm.dbeads.q = self.imm.beads.q + dev * counter
#                    qi.append(nmd * counter)
#                    dv = dstrip(self.imm.dforces.pots).copy()[0] / self.nprim - 0.50 * self.imm.w2[inm] * (nmd * counter)**2 - v0
#                    vi.append(v0 + 0.50 * self.imm.w2[inm] * (nmd * counter)**2 + dv)
#                    counter += 1
#                self.imm.dbeads.q += dev
#                vi.append(dstrip(self.imm.dforces.pots).copy()[0] / self.nprim)
#                self.imm.dbeads.q += dev
#                vi.append(dstrip(self.imm.dforces.pots).copy()[0] / self.nprim)
#
#                nptsmax[inm] = counter -1
#                npts[inm] = nptsmin[inm] + nptsmax[inm] + 1
#                vis.append(vi)
#                np.savetxt('qindeps.'+str(inm)+'.dat',qi)
#                np.savetxt('vindeps.'+str(inm)+'.dat',vi)
#            np.savetxt('nptsmin.dat',nptsmin, fmt='%i')
#            np.savetxt('nptsmax.dat',nptsmax, fmt='%i')
        # By replacing the previous paragraph with the bemedium one can enforce sampling of a regular symmetric 
        # grid of points. This almediums the output of the mapping to be fed in B. Monserrat implementation of IMF/VSCF.


        print "# NUMBER OF POINTS"
        print npts

        ## MAP OUT 2D BO SURFACES
        if self.threebody == False:
            print "# MAPPING OUT 2D BO SURFACES"
            ## RUN OVER MODES 
            rinm = -1
            for inm in inms:
                rinm += 1
    
                ## RUN OVER MODES 
                rjnm = -1
                for jnm in inms:
                    rjnm += 1
    
                    ## ONLY CONSIDER UNIQUE PAIRWISE COUPLINGS
                    if jnm > inm:
    
                        # if mapping has been perfomed previously, just load the potential from file
                        potfile = 'vcoupled.'+str(inm)+'.'+str(jnm)+'.dat'
                        if os.path.exists(potfile)==False:
                            vtots = np.zeros((npts[inm]+4)*(npts[jnm]+4))
                            nmis = np.zeros(npts[inm]+4)
                            nmjs = np.zeros(npts[jnm]+4)
                            dnmi = self.fnmrms * self.imm.nmrms[inm]
                            dnmj = self.fnmrms * self.imm.nmrms[jnm]
                           
                            # TIMINGS
                            TIME0 = time.time() 

                            for i in range(npts[inm]+4):
                                nmi = (-nptsmin[inm]+i-2.0)*dnmi
                                nmis[i] = nmi
                                for j in range(npts[jnm]+4):
                                    k = i * (npts[jnm]+4) + j
                                    nmj = (-nptsmin[jnm]+j-2.0)*dnmj
                                    nmjs[j] = nmj
   
                                    # on-nm-axis potentials are available from mapping in figuring out sampling range
                                    if (-nptsmin[inm]+i-2) == 0 :
                                        vtots[k] = vis[rjnm][j]
                                    elif (-nptsmin[jnm]+j-2) == 0 :
                                        vtots[k] = vis[rinm][i]
                                    # off-nm-axis potentials need to be evaluated
                                    else :
                                        self.imm.dbeads.q = dstrip(self.imm.beads.q) + np.real(self.imm.V.T[inm]) * nmis[i] * np.sqrt(self.nprim) + np.real(self.imm.V.T[jnm]) * nmjs[j] * np.sqrt(self.nprim)
                                        vtots[k] = dstrip(self.imm.dforces.pots)[0] / self.nprim
    
                            print "TIME TAKEN TO MAP THE POTENTIAL: ", time.time() - TIME0

                            # TIMINGS
                            TIME0 = time.time() 

                            # Store mapped coupling
                            if self.print_2b_map:
                                nmijs = np.zeros(((npts[inm]+4)*(npts[jnm]+4),3))
                                k = -1
                                for nmi in nmis:
                                    for nmj in nmjs:
                                        k += 1
                                        nmijs[k] = [nmi,nmj,vtots[k]-v0]
                                np.savetxt('vcoupledmap.'+str(inm)+'.'+str(jnm)+'.dat',nmijs)
                            print "TIME TAKEN TO SAVE THE COUPLING: ", time.time() - TIME0

                            # TIMINGS
                            TIME0 = time.time() 
    
                            #print hpyobject.heap()

                            # fit 1D and 2D cubic splines to sampled potentials
                            vtspl = interp2d(nmis, nmjs, vtots, kind='cubic', bounds_error=False)\

                            print "TIME TAKEN TO PERFORM INTERPOLATION: ", time.time() - TIME0
     
                            # Save integration grid for given pair of mode
                            igrid = np.linspace(-nptsmin[inm]*dnmi,nptsmax[inm]*dnmi,self.nint)
                            jgrid = np.linspace(-nptsmin[jnm]*dnmj,nptsmax[jnm]*dnmj,self.nint)
    
                            # TIMINGS
                            TIME0 = time.time()
 
                            # if mapping of 1D slice has been perfomed previously, skip printing grid to file
                            potfile = 'vindep.'+str(inm)+'.dat'
                            if os.path.exists(potfile)==False:
                                vigrid = np.asarray([np.asscalar(vtspl(igrid[iinm],0.0) - 0.5 * self.imm.w2[inm] * igrid[iinm]**2 - vtspl(0.0,0.0)) for iinm in range(self.nint)])
                                np.savetxt('vindep.'+str(inm)+'.dat',np.column_stack((igrid,vigrid)))
                            potfile = 'vindep.'+str(jnm)+'.dat'
                            if os.path.exists(potfile)==False:
                                vigrid = np.asarray([np.asscalar(vtspl(0.0,jgrid[ijnm]) - 0.5 * self.imm.w2[jnm] * jgrid[ijnm]**2 - vtspl(0.0,0.0)) for ijnm in range(self.nint)])
                                np.savetxt('vindep.'+str(jnm)+'.dat',np.column_stack((jgrid,vigrid)))

                            print "TIME TAKEN TO SAVE THE INTERPOLATED DATA: ", time.time() - TIME0
    
                            # Store coupling corr potentials in terms of integration grids
                            vijgrid = np.zeros((self.nint, self.nint))
                            vijgrid = (np.asarray( [ np.asarray( [ (vtspl(igrid[iinm],jgrid[ijnm]) - vtspl(igrid[iinm],0.0) - vtspl(0.0,jgrid[ijnm]) + vtspl(0.0,0.0)) for iinm in range(self.nint) ] ) for ijnm in range(self.nint) ] )).reshape((self.nint,self.nint))
    
                            # Save coupling correction to file for visualisation
                            np.savetxt('vcoupled.'+str(inm)+'.'+str(jnm)+'.dat',vijgrid)
    
                print "# GRIDDING DONE"

        ## MAP OUT 3D BO SURFACES
        else:
            print "# MAPPING OUT 3D BO SURFACES"
            for inm in inms:
                for jnm in inms:
                    ## ONLY CONSIDER UNIQUE COUPLINGS
                    if jnm > inm:
                        for knm in inms:
                            ## ONLY CONSIDER UNIQUE COUPLINGS
                            if knm > jnm:
                                potfile = 'vtriplet.'+str(inm)+'.'+str(jnm)+'.'+str(knm)+'.dat'
                                if os.path.exists(potfile)==False:
                                    vtots = np.zeros(((npts[inm]+4),(npts[jnm]+4),(npts[knm]+4)))
                                    vijgrid = np.zeros((self.nint, self.nint, self.nint))
                                    nmis = np.zeros(npts[inm]+4)
                                    nmjs = np.zeros(npts[jnm]+4)
                                    nmks = np.zeros(npts[knm]+4)
                                    dnmi = self.fnmrms * self.imm.nmrms[inm]
                                    dnmj = self.fnmrms * self.imm.nmrms[jnm]
                                    dnmk = self.fnmrms * self.imm.nmrms[knm]
            
                                    print "# MAPPING MODES ",inm,jnm,knm

                                    for i in range(npts[inm]+4):
                                        nmi = (-nptsmin[inm]+i-2.0)*dnmi
                                        nmis[i] = nmi
                                        for j in range(npts[jnm]+4):
                                            nmj = (-nptsmin[jnm]+j-2.0)*dnmj
                                            nmjs[j] = nmj
                                            for k in range(npts[knm]+4):
                                                nmk = (-nptsmin[knm]+k-2.0)*dnmk
                                                nmks[k] = nmk
                                                l = i * (npts[jnm]+4) * (npts[knm]+4) + j * (npts[knm]+4) + k

                                                # on-nm-plane potentials are available from mapping 2D slices
                                                # off-nm-plane potentials need to be evaluated
                                                self.imm.dbeads.q = dstrip(self.imm.beads.q) + np.real(self.imm.V.T[inm]) * nmis[i] * np.sqrt(self.nprim) + np.real(self.imm.V.T[jnm]) * nmjs[j] * np.sqrt(self.nprim) + np.real(self.imm.V.T[knm]) * nmks[k] * np.sqrt(self.nprim)
                                                vtots[i,j,k] = dstrip(self.imm.dforces.pots)[0] / self.nprim

                                    print "# MAPPING DONE"

                                    # simple linear interpolation of sampled potentials for now
                                    vtspl = RegularGridInterpolator((nmis, nmjs, nmks), vtots, bounds_error=False)

                                    print "# FITTING DONE"

                                    # Save integration grid for given pair of mode
                                    igrid = np.linspace(-nptsmin[inm]*dnmi,nptsmax[inm]*dnmi,self.nint)
                                    jgrid = np.linspace(-nptsmin[jnm]*dnmj,nptsmax[jnm]*dnmj,self.nint)
                                    kgrid = np.linspace(-nptsmin[knm]*dnmk,nptsmax[knm]*dnmk,self.nint)

                                    # print 1D slices to file (redo because if only two-body terms have previously been accounted for the fitting was done quite differently -- using cubic splines)
                                    vigrid = np.asarray([np.asscalar(vtspl([igrid[iinm],0.0,0.0]) - 0.5 * self.imm.w2[inm] * igrid[iinm]**2 - vtspl([0.0,0.0,0.0])) for iinm in range(self.nint)])
                                    np.savetxt('vindep.'+str(inm)+'.dat',np.column_stack((igrid,vigrid)))
                                    vigrid = np.asarray([np.asscalar(vtspl([0.0,jgrid[ijnm],0.0]) - 0.5 * self.imm.w2[jnm] * jgrid[ijnm]**2 - vtspl([0.0,0.0,0.0])) for ijnm in range(self.nint)])
                                    np.savetxt('vindep.'+str(jnm)+'.dat',np.column_stack((jgrid,vigrid)))
                                    vigrid = np.asarray([np.asscalar(vtspl([0.0,0.0,kgrid[iknm]]) - 0.5 * self.imm.w2[knm] * kgrid[iknm]**2 - vtspl([0.0,0.0,0.0])) for iknm in range(self.nint)])
                                    np.savetxt('vindep.'+str(knm)+'.dat',np.column_stack((kgrid,vigrid)))
        
                                    # print 2D slices to file
                                    vijgrid = (np.asarray( [ np.asarray( [ (vtspl([igrid[iinm],jgrid[ijnm],0.0]) - vtspl([igrid[iinm],0.0,0.0]) - vtspl([0.0,jgrid[ijnm],0.0]) + vtspl([0.0,0.0,0.0])) for iinm in range(self.nint) ] ) for ijnm in range(self.nint) ] )).reshape((self.nint,self.nint))
                                    np.savetxt('vcoupled.'+str(inm)+'.'+str(jnm)+'.dat',vijgrid)
                                    vijgrid = (np.asarray( [ np.asarray( [ (vtspl([igrid[iinm],0.0,kgrid[iknm]]) - vtspl([igrid[iinm],0.0,0.0]) - vtspl([0.0,0.0,kgrid[iknm]]) + vtspl([0.0,0.0,0.0])) for iinm in range(self.nint) ] ) for iknm in range(self.nint) ] )).reshape((self.nint,self.nint))
                                    np.savetxt('vcoupled.'+str(inm)+'.'+str(knm)+'.dat',vijgrid)
                                    vijgrid = (np.asarray( [ np.asarray( [ (vtspl([0.0,jgrid[ijnm],kgrid[iknm]]) - vtspl([0.0,jgrid[ijnm],0.0]) - vtspl([0.0,0.0,kgrid[iknm]]) + vtspl([0.0,0.0,0.0])) for ijnm in range(self.nint) ] ) for iknm in range(self.nint) ] )).reshape((self.nint,self.nint))
                                    np.savetxt('vcoupled.'+str(jnm)+'.'+str(knm)+'.dat',vijgrid)

                                    # Store coupling corr potentials in terms of integration grids
                                    vijkgrid = np.zeros(self.nint * self.nint * self.nint)
                                    vijkgrid = \
                                        (np.asarray( [ np.asarray( [ np.asarray( [ \
                                        ( vtspl([igrid[iinm],jgrid[ijnm],kgrid[iknm]]) 
                                        - ( vtspl([igrid[iinm],jgrid[ijnm],0.0]) + vtspl([igrid[iinm],0.0,kgrid[iknm]]) + vtspl([0.0,jgrid[ijnm],kgrid[iknm]]) )
                                        + ( vtspl([igrid[iinm],0.0,0.0]) + vtspl([0.0,jgrid[ijnm],0.0]) + vtspl([0.0,0.0,kgrid[iknm]]) )
                                        - vtspl([0.0,0.0,0.0]) ) \
                                        for iinm in range(self.nint) ] ) \
                                        for ijnm in range(self.nint) ] ) \
                                        for iknm in range(self.nint) ] )).reshape(self.nint*self.nint*self.nint)
                                    ijkgrid = np.zeros((self.nint * self.nint * self.nint, 3))
                                    ijkgrid = \
                                        (np.asarray( [ np.asarray( [ np.asarray( [ \
                                        [igrid[iinm], jgrid[ijnm], kgrid[iknm]]
                                        for iinm in range(self.nint) ] ) \
                                        for ijnm in range(self.nint) ] ) \
                                        for iknm in range(self.nint) ] )).reshape((self.nint*self.nint*self.nint,3))

                                    print "# GRIDDING DONE"

                                    # Save coupling correction to file for visualisation
                                    np.savetxt(potfile,np.column_stack((ijkgrid,vijkgrid)))



#==========================================================================
#==========================================================================


class VSCFSolver(IMF):
    """ 
    """

    def bind(self, imm):
        """ Reference all the variables for simpler access."""
        super(VSCFSolver, self).bind(imm)

        # calculates the r.m.s displacement along each mode 
        #x = np.exp(1.0 * np.sqrt(dstrip(self.imm.w2)) / dstrip(self.imm.temp))
        # not temperature dependent so that sampled potentials can easily be reused to evaluate free energy at different temp
        #self.imm.nmrms = np.sqrt((1 / (x - 1) + 0.5 ) / np.sqrt(self.imm.w2)) # harm RMS displacement along normal mode
        self.imm.nmrms = np.sqrt( 0.5 / np.sqrt(self.imm.w2)) # harm ZP RMS displacement along normal mode
        # not temperature dependent so that sampled potentials can easily be reused to evaluate free energy at different temp
        #self.imm.nmevib =  (0.5 + 1 / (x - 1)) * np.sqrt(dstrip(self.imm.w2)) # harm vibr energy at finite temp
        self.imm.nmevib = 0.5 * np.sqrt(dstrip(self.imm.w2)) # harm vibr energy at finite temp

        self.ethresh = self.imm.ethresh # convergence thresh for fractional error in vibr free energy
        self.nkbt = self.imm.nkbt # thresh for (e - e_gs)/(kB T) of vibr state to be incl in the VSCF and partition function
        self.nexc = self.imm.nexc # minimum number of excited n-body states to calculate (also in MP2 correction)
        self.mptwo = self.imm.mptwo # flag for calculation of MP2 correction
        self.print_mftpot = self.imm.print_mftpot # flag for printing of MFT anharmonic corrections to file
        self.threebody = self.imm.threebody
        self.print_vib_density = self.imm.print_vib_density

        self.total_anhar_free_energy = 0.0
        self.total_har_free_energy = 0.0

    def step(self, step=None):
        """Computes the Born Oppenheimer curve along a normal mode."""

        # soft exit if more than one step, because everything is calculated in one single step
        if step > 0:
            softexit.trigger("VSCF has finished in first (previous) step. Exiting.")

        # load indices of vibrational modes (excl transl and rot)
        if os.path.exists('modes.dat'):
            inms = np.loadtxt('modes.dat',dtype=int)
        else:
            softexit.trigger("ERROR : Indices for for vibr modes not available. Exiting.")
        # load number of sampled points along each mode
        npts = np.zeros(self.dof, dtype=int)
        nptsmin = np.zeros(self.dof, dtype=int)
        nptsmax = np.zeros(self.dof, dtype=int)
        if os.path.exists('nptsmin.dat'):
            nptsmin = np.loadtxt('nptsmin.dat')
            nptsmax = np.loadtxt('nptsmax.dat')
        else:
            softexit.trigger("ERROR : Indices for for vibr modes not available. Exiting.")
        npts = nptsmin + nptsmax + 1

        ## READ IN POTENTIAL OFFSET
        if os.path.exists('potoffset.dat'):
            self.v0 = np.loadtxt('potoffset.dat')
        else:
            softexit.trigger("ERROR : Potential offset v0 not available. Exiting.")

        ## READ IN INDPEPENDENT MODE POTENTIALS
        igrid = np.zeros((self.dof, self.nint))
        vigrid = np.zeros((self.dof, self.nint))
        for inm in inms:
            vifile = 'vindep.'+str(inm)+'.dat'
            if os.path.exists(vifile):
                vidata = np.loadtxt(vifile)
                igrid[inm] = vidata.T[0]
                vigrid[inm] = vidata.T[1]
            else:
                softexit.trigger("ERROR : Indep mode potential for mode "+str(inm)+" not available: vindep."+str(inm)+".dat. Exiting.")

        ## READ IN 2D MODE COUPLINGS
        vijgrid = np.zeros((self.dof, self.dof, self.nint, self.nint))
        for inm in inms:
            for jnm in inms:
                if jnm > inm:
                    vijfile = 'vcoupled.'+str(inm)+'.'+str(jnm)+'.dat'
                    if os.path.exists(vijfile):
                        vijgrid[inm][jnm] = np.loadtxt(vijfile)
                    else:
                        softexit.trigger("ERROR : Coupling potential for modes "+str(inm)+" , "+str(jnm)+" not available. Exiting.")
                else:
                    vijgrid[inm][jnm] = vijgrid[jnm][inm].T.copy()

        ## READ IN 3D MODE COUPLINGS
        if self.threebody:
            print "# ACCOUNTING FOR THREE-BODY TERMS"
            vijkgrid = np.zeros((self.dof, self.dof, self.dof, self.nint, self.nint, self.nint))
            for inm in inms:
                for jnm in inms:
                    if jnm > inm:
                        for knm in inms:
                            if knm > jnm:
                                # knm > jnm > inm
                                vijkfile = 'vtriplet.'+str(inm)+'.'+str(jnm)+'.'+str(knm)+'.dat'
                                if os.path.exists(vijkfile):
                                    vijkgrid[inm][jnm][knm] = ((np.loadtxt(vijkfile)).reshape((self.nint,self.nint,self.nint,4)).T[3]).T
                                else:
                                    softexit.trigger("ERROR : Coupling potential for modes "+str(inm)+" , "+str(jnm)+" , "+str(knm)+" not available. Exiting.")

            for inm in inms:
                for jnm in inms:
                    for knm in inms:
                        if knm > jnm and jnm > inm:
                            vijkgrid[inm][jnm][knm] = vijkgrid[inm][jnm][knm]
                        if knm > inm and inm > jnm:
                            vijkgrid[inm][jnm][knm] = np.transpose(vijkgrid[inm][jnm][knm], (1,0,2))
                        if jnm > knm and knm > inm:
                            vijkgrid[inm][jnm][knm] = np.transpose(vijkgrid[inm][jnm][knm], (0,2,1))
                        if jnm > inm and inm > knm:
                            vijkgrid[inm][jnm][knm] = np.transpose(vijkgrid[inm][jnm][knm], (2,0,1))
                        if inm > knm and knm > jnm:
                            vijkgrid[inm][jnm][knm] = np.transpose(vijkgrid[inm][jnm][knm], (1,2,0))
                        if inm > jnm and jnm > knm:
                            vijkgrid[inm][jnm][knm] = np.transpose(vijkgrid[inm][jnm][knm], (2,1,0))

        ## SET UP WVFN BASIS
        # initializes the SHO wvfn basis for solving the 1D Schroedinger equ
        hf = []
        for i in xrange(self.nbasis):
            hf.append(hermite(i))

        def psi(n,mass,freq,x):
            nu = mass * freq
            norm = (nu/np.pi)**0.25 * np.sqrt(1.0/(2.0**(n)*np.math.factorial(n)))
            psival = norm * np.exp(-nu * x**2 /2.0) * hf[n](np.sqrt(nu)*x)
            return psival

        psigrid = np.zeros((self.dof, self.nint, self.nbasis))
        psiigrid = np.zeros((self.dof, self.nbasis, self.nbasis, self.nint))
        norm = np.zeros((self.dof, self.nbasis))
        mass = 1.0
        for inm in inms:
            for ibasis in xrange(self.nbasis):
                for iinm in xrange(self.nint):
                    psigrid[inm][iinm][ibasis] = psi(ibasis,mass,np.sqrt(self.imm.w2[inm]), igrid[inm][iinm])
                norm[inm][ibasis] = np.sum(psigrid[inm,:,ibasis]**2)
                psigrid[inm,:,ibasis] /= np.sqrt(norm[inm][ibasis])
            for ibasis in xrange(self.nbasis):
                for jbasis in xrange(self.nbasis):
                    for iinm in xrange(self.nint):
                        psiigrid[inm][ibasis][jbasis][iinm] = psi(ibasis,mass,np.sqrt(self.imm.w2[inm]), igrid[inm][iinm]) * psi(jbasis,mass,np.sqrt(self.imm.w2[inm]), igrid[inm][iinm])
                    psiigrid[inm][ibasis][jbasis] /= np.sqrt(norm[inm][ibasis]*norm[inm][jbasis])
            print "# WVFN INITIALISED"

        ## SAVE HARMONIC FREQUENCIES TO FILE FOR VISUALISATIONS
        np.savetxt('harfreqs.dat',np.sqrt(self.imm.w2))

        ## INDEPENDENT MODE APPROXIMATION
        evals = np.zeros((self.dof, self.nbasis))
        evecs = np.zeros((self.dof, self.nbasis, self.nbasis))
        ai = np.zeros(self.dof)
        ## RUN OVER MODES 
        for inm in inms:

            # skip mode if frequency indicates a translation/rotation
            if np.abs(self.imm.w2[inm]) < 1e-10:
                print "# IGNORING THE NM. FREQUENCY IS SMALLER THEN 2 cm^-1"
                break

            # Construct the Hamiltonian matrix
            h = np.zeros((self.nbasis,self.nbasis))
            for ibasis in xrange(self.nbasis):
                for jbasis in xrange(ibasis,self.nbasis,1):
                    h[ibasis][jbasis] = np.dot(psiigrid[inm][ibasis][jbasis], vigrid[inm])
                h[ibasis][ibasis] *= 0.5
                h[ibasis][ibasis] += 0.5 * (ibasis + 0.5) * np.sqrt(self.imm.w2[inm])
            h += h.T # fill in the mediumer triangle 

            # Diagonalise Hamiltonian matrix
            evals[inm], U = np.linalg.eigh(h)
            evecs[inm] = U.T
            # Evaluate anharmonic free energy 
            ai[inm] = -logsumexp(-1.0 * evals[inm] / dstrip(self.imm.temp))*dstrip(self.imm.temp)

            print '# INDEPENDENT MODE ',inm,' FREE ENERGY: ',ai[inm]

        if self.mptwo:
            ## PREPARE EIGENSTATES OF COMPLETE SYSTEM
            # check mediumest relevant independent mode excitation
            nmbasis = []
            for inm in inms:
                nbasiscurr = 0
                for ibasis in range(self.nbasis):
                    if (evals[inm][ibasis] - evals[inm][0]) < self.nkbt * dstrip(self.imm.temp):
                        nbasiscurr += 1
                nmbasis.append(range(nbasiscurr))
        
            nstates = np.product([len(nmbasis[i]) for i in range(len(nmbasis))])
            print '# NUMBERS OF INDEPENDENT MODE EIGENSTATES CONSIDERED IN ENUMERATION OF FEASIBLE GLOBAL EIGENSTATES: ', nstates, nmbasis
            egs = np.sum(np.asarray([evals[inm][0] for inm in inms]))
            sred = nmbasis[0]
            for inm in range(len(inms)):
                if inm == 0: continue
                listtmp = []
                listtmp.append(sred)
                listtmp.append(nmbasis[inm])
                stmp = list(itertools.product(*listtmp))
                if inm > 1:
                    for el in range(len(stmp)):
                        stmp[el] = stmp[el][0] + (stmp[el][1],)
                stmp = list(stmp)
                sred = []
                ered = []
                for state in stmp:
                    e = np.sum(np.asarray([evals[inms[jnm]][state[jnm]] for jnm in range(inm+1)]))
                    e -= np.sum(np.asarray([evals[inms[jnm]][0] for jnm in range(inm+1)]))
                    ## DETERMINE FROM IMF ENERGIES WHICH CONTRIBUTE SIGNIFICANTLY TO PARTITION FUNCTION
                    if e > self.nkbt * dstrip(self.imm.temp): continue
                    sred.append(state)
                    ered.append(e + egs)

                if len(sred) > 1e3:
                    print 'TOO MANY (>1e3) POSSIBLE GLOBAL VIBRATIONAL STATES'
                else:
                    print 'AMONG THE FIRST',inm+1,'MODES WE CONSIDER',len(sred),'STATE(S)'

            nstates = len(sred)
            s = np.zeros((nstates,self.dof),dtype=int)
            for istate in range(nstates):
                s[istate][inms] = sred[istate]

            print "# SOLVING VSCF PROBLEM FOR", nstates, "STATE(S)!"

        # START WITH INDEPENDENT MODE THERMAL VIBRATIONAL DENSITY
        evalsvscf = evals.copy()
        evecsvscf = evecs.copy()

        aharmfull = np.sum((0.5 * np.sqrt(self.imm.w2[inms]) + self.imm.temp * np.log(1.0 - np.exp(-np.sqrt(self.imm.w2[inms]) / self.imm.temp))))
        aindepfull = np.sum(np.asarray([ -logsumexp(-1.0 * evals[inm] / dstrip(self.imm.temp)) for inm in inms])) * dstrip(self.imm.temp)
        eharmfull = np.sum(np.sqrt(self.imm.w2[inms]) * (0.5 + 1.0 / (np.exp(np.sqrt(self.imm.w2[inms]) / self.imm.temp) -1)))
        eindepfull = np.sum(np.asarray([ np.sum(evals[inm] * np.exp(-1.0 * evals[inm] / dstrip(self.imm.temp))) / np.sum(np.exp(-1.0 * evals[inm] / dstrip(self.imm.temp))) for inm in inms]))

        if self.mptwo:
            evaltotharm = np.asarray([ np.sum(np.asarray([ (0.5 + s[istate][inm])*np.sqrt(self.imm.w2[inm]) for inm in inms ])) for istate in range(nstates)])
            evaltotindep = np.asarray([ np.sum(np.asarray([ evals[inm][s[istate][inm]] for inm in inms ])) for istate in range(nstates)])
            aharm = -logsumexp(-1.0 * np.asarray(evaltotharm) / dstrip(self.imm.temp)) * dstrip(self.imm.temp)
            aindep = -logsumexp(-1.0 * np.asarray(evaltotindep) / dstrip(self.imm.temp)) * dstrip(self.imm.temp)
            eharm = np.sum((0.5 * np.sqrt(self.imm.w2[inms]) + self.imm.temp * np.log(1.0 - np.exp(-np.sqrt(self.imm.w2[inms]) / self.imm.temp))))
            eindep = np.sum(np.asarray(evaltotindep) * np.exp(-1.0 * (np.asarray(evaltotindep)-np.asarray(evaltotindep)[0]) / dstrip(self.imm.temp)))
            eindep /= np.sum(np.exp(-1.0 * (np.asarray(evaltotindep)-np.asarray(evaltotindep)[0]) / dstrip(self.imm.temp)))
        else:
            aharm = aharmfull
            aindep = aindepfull
            eharm = eharmfull
            eindep = eindepfull

        aold = aindep.copy()

        print 'Iteration = HAR , A = ',aharm#,np.asarray([np.sqrt(self.imm.w2[1])*(0.5+i) for i in range(3)]),np.asarray([np.sqrt(self.imm.w2[2])*(0.5+i) for i in range(3)])
        print 'Iteration = IMF , A = ',aindep#,evals[1][:3],evals[2][:3]

        # MF potentials initialised to the independent mode potentials
        vmft = vigrid.copy()

        # fraction of new/updated MFT potential mixed into that from the previous VSCF step
        alphamix = 0.5

        iiter = 0
        while True:
            iiter += 1

            # 1D density
            rhoigrid = np.zeros((self.dof, self.nint))
            for inm in inms:
                # reduced thermal vibrational density just accounting for the reduced number of global vibrational eigenstates
                #for istate in range(nstates):
                #    rhoigrid[inm] += np.exp(-1.0 * np.sum(np.asarray([ (evalsvscf[jnm][s[istate][jnm]] - evalsvscf[jnm][s[0][jnm]]) for jnm in inms ])) / dstrip(self.imm.temp)) \
                #    * np.dot(psigrid[inm],evecsvscf[inm][s[istate][inm]])**2
                #rhoigrid[inm] /= np.sum(rhoigrid[inm])

                for ibasis in range(self.nbasis):
                    rhoigrid[inm] += np.exp(-1.0 * (evalsvscf[inm][ibasis] - evalsvscf[inm][0]) / dstrip(self.imm.temp)) \
                    * np.dot(psigrid[inm],evecsvscf[inm][ibasis])**2
                rhoigrid[inm] /= np.sum(rhoigrid[inm])

                if iiter == 1:
                    np.savetxt('rho_indep.'+str(inm)+'.dat',np.c_[igrid[inm],rhoigrid[inm]])
                else:
                    np.savetxt('rho_mft.'+str(inm)+'.dat',np.c_[igrid[inm],rhoigrid[inm]])
            # 2D density
            if self.threebody:
                rhoijgrid = np.zeros((self.dof, self.dof, self.nint, self.nint))
                for inm in inms:
                    for jnm in inms:
                        if jnm == inm:
                            continue
                        # reduced thermal vibrational density just accounting for the reduced number of global vibrational eigenstates
                        #for istate in range(nstates):
                        #    rhoijgrid[inm] += np.exp(-1.0 * np.sum(np.asarray([ (evalsvscf[jnm][s[istate][jnm]] - evalsvscf[jnm][s[0][jnm]]) for knm in inms ])) / dstrip(self.imm.temp)) \
                        #            * np.outer(np.dot(psigrid[inm],evecsvscf[inm][s[istate][inm]])**2 , \
                        #            np.dot(psigrid[jnm],evecsvscf[jnm][s[istate][jnm]])**2)
                        #rhoijgrid[inm,jnm] /= np.sum(rhoijgrid[inm,jnm])

                        rhoijgrid[inm,jnm] = np.outer(rhoigrid[inm],rhoigrid[jnm])

                        if iiter == 1:
                            np.savetxt('rho_indep_pair.'+str(inm)+'.'+str(jnm)+'.dat',rhoijgrid[inm,jnm])
                        else:
                            np.savetxt('rho_mft_pair.'+str(inm)+'.'+str(jnm)+'.dat',rhoijgrid[inm,jnm])

            ## RUN OVER MODES 
            for inm in inms:
                # MF potential that mode inm lives in
                #vmft = np.zeros(vigrid[inm].shape)
                #vmft += vigrid[inm]
                vmft[inm] = vigrid[inm] + (1.0 - alphamix) * (vmft[inm] - vigrid[inm])
                for jnm in inms:
                    # avoid mode coupling to itself
                    if jnm == inm:
                        continue
                    # sum up all MFT two-body contributions
                    vmft[inm] += alphamix * np.dot(vijgrid[inm][jnm].T,rhoigrid[jnm]) / self.nprim

                    # if three-body terms are to be accounted for
                    if self.threebody:
                        for knm in inms:
                            if knm == inm or knm == jnm:
                                continue
                            # sum up all MFT three-body contributions
                            vmft[inm] += 0.5 * alphamix * np.sum(vijkgrid[inm][jnm][knm] * rhoijgrid[jnm,knm]) / self.nprim**2 # eae : to check whether vijkgrid needs to be transposed

                # Save MFT anharmonic correction to file for visualisation
                if self.print_mftpot:
                    if self.threebody == False:
                        np.savetxt('vmft_twobody.'+str(inm)+'.dat',np.column_stack((igrid[inm],vigrid[inm],vmft[inm])))
                    else:
                        np.savetxt('vmft_threebody.'+str(inm)+'.dat',np.column_stack((igrid[inm],vigrid[inm],vmft[inm])))

                # Construct Hamiltonian matrices
                h = np.zeros((self.nbasis,self.nbasis))
                for ibasis in xrange(self.nbasis):
                    for jbasis in xrange(ibasis,self.nbasis,1):
                        # mode inm MFT Hamiltonian
                        h[ibasis][jbasis] = np.dot(psiigrid[inm][ibasis][jbasis], vmft[inm])
                    h[ibasis][ibasis] *= 0.5
                    h[ibasis][ibasis] += 0.5 * (ibasis + 0.5) * np.sqrt(self.imm.w2[inm])
                h += h.T # fill in the mediumer triangle

                # Diagonalise Hamiltonian matrix given mode 0 in state s0 and mode 1 in state s1
                evalsvscf[inm], U = np.linalg.eigh(h)
                evecsvscf[inm] = U.T

            # Loop over all modes within an VSCF step completed

            # Calculate current MFT free energy
            if self.mptwo:
                evaltotcoupled = np.asarray([np.sum([ evalsvscf[inm][s[istate][inm]] for inm in inms ]) for istate in range(nstates)])
                acoupled = -logsumexp(-1.0 * evaltotcoupled / dstrip(self.imm.temp)) * dstrip(self.imm.temp)
            else:
                acoupled = np.sum(np.asarray([ -logsumexp(-1.0 * evalsvscf[inm] / dstrip(self.imm.temp)) for inm in inms])) * dstrip(self.imm.temp)

            ## CHECK THAT FREE ENERGY HAS CONVERGED
            print 'Iteration =',iiter,' A = ',acoupled#,evalsvscf[1][:3],evalsvscf[2][:3]
            if iiter > 5:
                if ( np.abs(acoupled - aold)/np.abs(aold) < self.athresh):
                    break
                if iiter > 200:
                    print 'WARNING WARNING WARNING : VSCF DID NOT CONVERGE'
                    break
            aold = acoupled.copy()

        ## ALREADY PRINT OUT IN CASE MP2 CRASHES
        if self.mptwo:
            ecoupled = np.sum(np.asarray(evaltotcoupled) * np.exp(-1.0 * (np.asarray(evaltotcoupled)-np.asarray(evaltotcoupled)[0]) / dstrip(self.imm.temp)))
            ecoupled /= np.sum(np.exp(-1.0 * (np.asarray(evaltotcoupled)-np.asarray(evaltotcoupled)[0]) / dstrip(self.imm.temp)))
        else:
            ecoupled = np.sum(np.asarray([ np.sum(evalsvscf[inm] * np.exp(-1.0 * evalsvscf[inm] / dstrip(self.imm.temp))) / np.sum(np.exp(-1.0 * evalsvscf[inm] / dstrip(self.imm.temp))) for inm in inms]))

        print 'POTENTIAL OFFSET          = ', self.v0
        print ''
        print 'FULL HAR FREE ENERGY      = ', aharmfull / self.nprim + self.v0
        print 'FULL IMF FREE ENERGY      = ', aindepfull / self.nprim + self.v0
        print 'HAR FREE ENERGY           = ', aharm / self.nprim + self.v0
        print 'IMF FREE ENERGY           = ', aindep / self.nprim + self.v0
        print 'VSCF FREE ENERGY CORR     = ', (acoupled - aindep) / self.nprim
        print ''
        print 'FULL HAR INTERNAL ENERGY  = ', eharmfull / self.nprim + self.v0
        print 'FULL IMF INTERNAL ENERGY  = ', eindepfull / self.nprim + self.v0
        print 'HAR INTERNAL ENERGY       = ', eharm / self.nprim + self.v0
        print 'IMF INTERNAL ENERGY       = ', eindep / self.nprim + self.v0
        print 'VSCF INTERNAL ENERGY CORR = ', (ecoupled - eindep) / self.nprim
        print ''


        if self.mptwo:
            ## PERFORM PERTURBATION THEORY AT MP2 LEVEL
            print "# MP2 PERTURBATION THEORY"
    
            # Integration grid for mode inm
            jgrid = np.zeros((self.dof,self.nint))
            for jnm in inms :
                dnmj = self.fnmrms * self.imm.nmrms[jnm]
                jgrid[jnm] = np.linspace(-nptsmin[jnm]*dnmj,nptsmax[jnm]*dnmj,self.nint)
    
            # START WITH GS
            devaltotcoupledmp2 = []
            for istate in range(nstates):
    
                # Psi[istate] : for inm in inms : wvfn for mode inm given state istate
                Psigrid = np.zeros((self.dof,self.nint))
                Normi = np.zeros(self.dof)
                # MF correction for potential of mode inm given that the overall state is s[istate]
                dvmfti = np.zeros((self.dof,self.nint))
                for inm in inms : 
                    # anharmonic MFT wvfn for mode inm normalised for the given integration grid
                    Psigrid[inm] = np.dot((psigrid[inm]/np.sqrt(np.sum(psigrid[inm]**2, axis=0))),evecsvscf[inm][s[istate][inm]])
    
                    # MF potential that mode inm lives in
                    vmft = np.zeros(self.nint)
                    for jnm in inms:
                        # skip mode if frequency indicates a translation/rotation
                        if jnm == inm:
                            continue
                        # sum up all MFT contributions
                        psjgrid = np.dot((psigrid[jnm]/np.sqrt(np.sum(psigrid[jnm]**2, axis=0))),evecsvscf[jnm][s[istate][jnm]])
                        vmft += np.dot(vijgrid[inm][jnm].T,psjgrid**2)
                    dvmfti[inm] = vmft
    
                demptwo = 0.0
                for jstate in range(nstates):
                    # only consider states different from istate
                    if jstate == istate: 
                        continue
                    
                    # Psi[jstate] : for jnm in inms : wvfn for mode jnm given state jstate
                    Psjgrid = np.zeros((self.dof,self.nint))
                    Normj = np.zeros(self.dof)
                    for jnm in inms : 
                        Psjgrid[jnm] = np.dot((psigrid[jnm]/np.sqrt(np.sum(psigrid[jnm]**2, axis=0))),evecsvscf[jnm][s[jstate][jnm]])
    
                    # V - VMFT --> sum_j (vijgrid - dvmfti)
                    matel = 0.0
                    matelmft = 0.0
                    matelcpl = 0.0
                    for inm in inms :
                        # Coupled part of matrix element
                        for jnm in inms :
                            if jnm == inm:
                                continue
                            # terms NOT involved in the current pairwise coupling
                            matel1 = 1.0
                            for knm in inms:
                                if knm == inm or knm == jnm:
                                    continue
                                matel1 *= np.dot(evecsvscf[knm][s[jstate][knm]], evecsvscf[knm][s[istate][knm]])
                            # terms involved in the current pairwise coupling
                            matel2 = np.sum( np.outer(Psjgrid[inm], Psjgrid[jnm]) * vijgrid[inm][jnm].T * np.outer(Psigrid[inm], Psigrid[jnm]))
                            matelcpl += 0.5 * matel1 * matel2
                        # MFT part of matrix element
                        matel3 = 1.0
                        for knm in inms:
                            if knm == inm:
                                continue
                            matel3 *= np.dot(evecsvscf[knm][s[jstate][knm]], evecsvscf[knm][s[istate][knm]])
                        matel3 *= np.sum( Psjgrid[inm] * dvmfti[inm] * Psigrid[inm] )
                        matelmft += matelmft 
                        #matelmft += matel3 #eae32
                    matel = matelcpl - matelmft
                    jdemptwo = matel**2 / (evaltotcoupled[istate]-evaltotcoupled[jstate])
                    demptwo += jdemptwo
                devaltotcoupledmp2.append(demptwo)
    
            ## PRINT OUT FINAL RESULTS
            acoupledmp2 = -logsumexp(-1.0 * (np.asarray(evaltotcoupled) + np.asarray(devaltotcoupledmp2)) / dstrip(self.imm.temp)) * dstrip(self.imm.temp)
            print 'MP2 CORRECTION        = ', (acoupledmp2 - acoupled)/self.nprim

        print 'ALL QUANTITIES PER PRIMITIVE UNIT CELL (WHERE APPLICABLE)'

