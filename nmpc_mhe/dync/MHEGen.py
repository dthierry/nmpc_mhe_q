#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import print_function
from __future__ import division

from pyomo.core.base import Var, Objective, minimize, value, Set, Constraint, Expression, Param, Suffix, ConstraintList
from pyomo.core.base.sets import SimpleSet
from pyomo.opt import SolverFactory, ProblemFormat, SolverStatus, TerminationCondition
from nmpc_mhe.dync.DynGen import DynGen
from nmpc_mhe.dync.NMPCGen import NmpcGen
import numpy as np
from itertools import product
import sys

__author__ = "David M Thierry @dthierry"
"""Not yet. Our people, they don't understand."""


class MheGen(NmpcGen):
    def __init__(self, **kwargs):
        NmpcGen.__init__(self, **kwargs)

        # Need a list of relevant measurements y

        self.y = kwargs.pop('y', [])
        self.y_vars = kwargs.pop('y_vars', {})

        # Need a list or relevant noisy-states z

        self.x_noisy = kwargs.pop('x_noisy', [])
        self.x_vars = kwargs.pop('x_vars', {})
        self.deact_ics = kwargs.pop('del_ics', True)
        self.diag_Q_R = kwargs.pop('diag_QR', True)  #: By default use diagonal matrices for Q and R matrices
        self.u = kwargs.pop('u', [])

        print("-" * 120)
        print("I[[create_lsmhe]] lsmhe (full) model created.")
        print("-" * 120)

        self.lsmhe = self.d_mod(self.nfe_t, self.ncp_t, _t=self._t)
        self.lsmhe.name = "lsmhe (Least-Squares MHE)"
        self.lsmhe.create_bounds()
        #: create x_pi constraint

        #: Create list of noisy-states vars
        self.xkN_l = []
        self.xkN_nexcl = []
        self.xkN_key = {}
        k = 0
        for x in self.x_noisy:
            n_s = getattr(self.lsmhe, x)  #: Noisy-state
            for jth in self.x_vars[x]:  #: the jth variable
                self.xkN_l.append(n_s[(1, 0) + jth])
                self.xkN_nexcl.append(1)  #: non-exclusion list for active bounds
                self.xkN_key[(x, jth)] = k
                k += 1

        self.lsmhe.xkNk_mhe = Set(initialize=[i for i in range(0, len(self.xkN_l))])  #: Create set of noisy_states
        self.lsmhe.x_0_mhe = Param(self.lsmhe.xkNk_mhe, initialize=0.0, mutable=True)  #: Prior-state
        self.lsmhe.wk_mhe = Var(self.lsmhe.fe_t, self.lsmhe.xkNk_mhe, initialize=0.0)  #: Model disturbance
        self.lsmhe.PikN_mhe = Param(self.lsmhe.xkNk_mhe, self.lsmhe.xkNk_mhe,
                                initialize=lambda m, i, ii: 1. if i == ii else 0.0, mutable=True)  #: Prior-Covariance
        self.lsmhe.Q_mhe = Param(range(1, self.nfe_t), self.lsmhe.xkNk_mhe, initialize=1, mutable=True) if self.diag_Q_R\
            else Param(range(1, self.nfe_t), self.lsmhe.xkNk_mhe, self.lsmhe.xkNk_mhe,
                             initialize=lambda m, t, i, ii: 1. if i == ii else 0.0, mutable=True)  #: Disturbance-weight

        #: Create list of measurements vars
        self.yk_l = {}
        self.yk_key = {}
        k = 0
        self.yk_l[1] = []
        for y in self.y:
            m_v = getattr(self.lsmhe, y)  #: Measured "state"
            for jth in self.y_vars[y]:  #: the jth variable
                self.yk_l[1].append(m_v[(1, self.ncp_t) + jth])
                self.yk_key[(y, jth)] = k
                k += 1

        for t in range(2, self.nfe_t + 1):
            self.yk_l[t] = []
            for y in self.y:
                m_v = getattr(self.lsmhe, y)  #: Measured "state"
                for jth in self.y_vars[y]:  #: the jth variable
                    self.yk_l[t].append(m_v[(1, self.ncp_t) + jth])

        self.lsmhe.ykk_mhe = Set(initialize=[i for i in range(0, len(self.yk_l[1]))])  #: Create set of measured_vars
        self.lsmhe.nuk_mhe = Var(self.lsmhe.fe_t, self.lsmhe.ykk_mhe, initialize=0.0)   #: Measurement noise
        self.lsmhe.yk0_mhe = Param(self.lsmhe.fe_t, self.lsmhe.ykk_mhe, initialize=1.0, mutable=True)
        self.lsmhe.hyk_c_mhe = Constraint(self.lsmhe.fe_t, self.lsmhe.ykk_mhe,
                                      rule=lambda mod, t, i: mod.yk0_mhe[t, i] - self.yk_l[t][i] - mod.nuk_mhe[t, i] == 0.0)
        self.lsmhe.R_mhe = Param(self.lsmhe.fe_t, self.lsmhe.ykk_mhe, initialize=1.0, mutable=True) if self.diag_Q_R else \
            Param(self.lsmhe.fe_t, self.lsmhe.ykk_mhe, self.lsmhe.ykk_mhe,
                             initialize=lambda mod, t, i, ii: 1.0 if i == ii else 0.0, mutable=True)

        #: Deactivate icc constraints
        if self.deact_ics:
            pass
            # for i in self.states:
                # self.lsmhe.del_component(i + "_icc")
        #: Maybe only for a subset of the states
        else:
            for i in self.states:
                if i in self.x_noisy:
                    ic_con = getattr(self.lsmhe, i + "_icc")
                    for k in ic_con.keys():
                        if k[2:] in self.x_vars[i]:
                            ic_con[k].deactivate()
        #: Put the noise in the continuation equations (finite-element)
        j = 0
        for i in self.x_noisy:
            cp_con = getattr(self.lsmhe, "cp_" + i)
            cp_exp = getattr(self.lsmhe, "noisy_" + i)
            for k in self.x_vars[i]:  #: This should keep the same order
                for t in range(1, self.nfe_t):
                    cp_con[t, k].set_value(cp_exp[t, k] == self.lsmhe.wk_mhe[t, j])
                j += 1

        #: Expressions for the objective function (least-squares)
        self.lsmhe.Q_e_mhe = Expression(
            expr=0.5 * sum(
                sum(
                    self.lsmhe.Q_mhe[i, k] * self.lsmhe.wk_mhe[i, k]**2 for k in self.lsmhe.xkNk_mhe)
                for i in range(1, self.nfe_t))) if self.diag_Q_R else Expression(
            expr=sum(sum(self.lsmhe.wk_mhe[i, j] *
                         sum(self.lsmhe.Q_mhe[i, j, k] * self.lsmhe.wk_mhe[i, k] for k in self.lsmhe.xkNk_mhe)
                         for j in self.lsmhe.xkNk_mhe) for i in range(1, self.nfe_t)))

        self.lsmhe.R_e_mhe = Expression(
            expr=0.5 * sum(
                sum(
                    self.lsmhe.R_mhe[i, k] * self.lsmhe.nuk_mhe[i, k]**2 for k in self.lsmhe.xkNk_mhe)
                for i in self.lsmhe.fe_t)) if self.diag_Q_R else Expression(
            expr=sum(sum(self.lsmhe.nuk_mhe[i, j] *
                         sum(self.lsmhe.R_mhe[i, j, k] * self.lsmhe.nuk_mhe[i, k] for k in self.lsmhe.xkNk_mhe)
                         for j in self.lsmhe.xkNk_mhe) for i in self.lsmhe.fe_t))

        self.lsmhe.Arrival_e_mhe = Expression(
            expr=0.5 * sum((self.xkN_l[j] - self.lsmhe.x_0_mhe[j]) *
                     sum(self.lsmhe.PikN_mhe[j, k] * (self.xkN_l[k] - self.lsmhe.x_0_mhe[k]) for k in self.lsmhe.xkNk_mhe)
                     for j in self.lsmhe.xkNk_mhe))

        self.lsmhe.obfun_dum_mhe = Objective(sense=minimize,
                                             expr=self.lsmhe.Q_e_mhe + self.lsmhe.R_e_mhe)
        self.lsmhe.obfun_dum_mhe.activate()

        self.lsmhe.obfun_mhe = Objective(sense=minimize,
                                         expr=self.lsmhe.Arrival_e_mhe + self.lsmhe.Q_e_mhe + self.lsmhe.R_e_mhe)
        self.lsmhe.obfun_mhe.deactivate()

        self._PI = {}  #: Container of the KKT matrix
        self.xreal_W = {}

        self.s_estimate = dict.fromkeys(self.x_noisy)

    def initialize_xreal(self, ref):
        """Wanted to keep the states in a horizon-like window, this should be done in the main dyngen class"""
        dum = self.d_mod(1, self.ncp_t, _t=self.hi_t)
        dum.name = "Dummy [xreal]"
        self.load_d_d(ref, dum, 1)
        for fe in range(1, self._window_keep):
            for i in self.states:
                pn = i + "_ic"
                p = getattr(dum, pn)
                vs = getattr(dum, i)
                for ks in p.iterkeys():
                    p[ks].value = value(vs[(1, self.ncp_t) + (ks,)])
            #: Solve
            self.solve_d(dum, o_tee=False)
            for i in self.states:
                self.xreal_W[(i, fe)] = []
                xs = getattr(dum, i)
                for k in xs.keys():
                    if k[1] == self.ncp_t:
                        print(i)
                        self.xreal_W[(i, fe)].append(value(xs[k]))

    def init_lsmhe_prep(self, ref):
        """Initializes the lsmhe in preparation phase
        Args:
            ref (pyomo.core.base.PyomoModel.ConcreteModel): The reference model"""
        self.journalizer("I", self._c_it, "initialize_lsmhe", "Attempting to initialize lsmhe")
        dum = self.d_mod(1, self.ncp_t, _t=self.hi_t)
        dum.name = "Dummy I"
        #: Load current solution
        self.load_d_d(ref, dum, 1)
        #: Patching of finite elements
        for finite_elem in range(1, self.nfe_t + 1):
            #: Cycle ICS
            for i in self.states:
                pn = i + "_ic"
                p = getattr(dum, pn)
                vs = getattr(dum, i)
                for ks in p.iterkeys():
                    p[ks].value = value(vs[(1, self.ncp_t) + (ks,)])
            if finite_elem == 1:
                for i in self.states:
                    pn = i + "_ic"
                    p = getattr(self.lsmhe, pn)  #: Target
                    vs = getattr(dum, i)  #: Source
                    for ks in p.iterkeys():
                        p[ks].value = value(vs[(1, self.ncp_t) + (ks,)])
            self.extract_meas_(finite_elem)
            #: Solve
            self.solve_d(dum, o_tee=False)
            #: Patch
            self.load_d_d(dum, self.lsmhe, finite_elem)
            self.load_inputsmhe(dum, finite_elem)
        self.journalizer("I", self._c_it, "initialize_lsmhe", "Attempting to initialize lsmhe Done")

    def extract_meas_(self, t, **kwargs):
        """Mechanism to assign a value of y0 to the current mhe from the dynamic model
        Args:
            t (int): int The current collocation point
        Returns:
            meas_dict (dict): A dictionary containing the measurements list by meas_var
        """
        src = kwargs.pop("src", self.d1)
        skip_update = kwargs.pop("skip_update", False)

        meas_dic = dict.fromkeys(self.y)
        l = []
        for i in self.y:
            lm = []
            var = getattr(src, i)
            for j in self.y_vars[i]:
                lm.append(value(var[(1, self.ncp_t,) + j]))
                l.append(value(var[(1, self.ncp_t,) + j]))
            meas_dic[i] = lm

        if not skip_update:  #: Update the mhe model
            y0dest = getattr(self.lsmhe, "yk0_mhe")

            for i in self.y:
                for j in self.y_vars[i]:
                    k = self.yk_key[(i, j)]
                    y0dest[t, k] = l[k]
        return meas_dic

    def adjust_nu0_mhe(self):
        """Adjust the initial guess for the nu variable"""
        for t in self.lsmhe.fe_t:
            k = 0
            for i in self.y:
                for j in self.y_vars[i]:
                    target = value(self.lsmhe.yk0_mhe[t, k]) - value(self.yk_l[t][k])
                    self.lsmhe.nuk_mhe[t, k].set_value(target)
                    k += 1

    def set_covariance_meas(self, cov_dict):
        """Sets covariance(inverse) for the measurements.
        Args:
            cov_dict (dict): a dictionary with the following key structure [(meas_name, j), (meas_name, k), time]
        Returns:
            None
        """
        rtarget = getattr(self.lsmhe, "R_mhe")
        for key in cov_dict:
            vni = key[0]
            vnj = key[1]
            _t = key[2]

            v_i = self.yk_key[vni]
            v_j = self.yk_key[vnj]
            # try:
            if self.diag_Q_R:
                rtarget[_t, v_i] = 1 / cov_dict[vni, vnj, _t]
            else:
                rtarget[_t, v_i, v_j] = cov_dict[vni, vnj, _t]
            # except KeyError:
            #     print("Key error, {:} {:} {:}".format(vni, vnj, _t))

    def set_covariance_disturb(self, cov_dict):
        """Sets covariance(inverse) for the measurements.
        Args:
            cov_dict (dict): a dictionary with the following key structure [(meas_name, j), (meas_name, k), time]
        Returns:
            None
        """
        qtarget = getattr(self.lsmhe, "Q_mhe")
        for key in cov_dict:
            vni = key[0]
            vnj = key[1]
            _t = key[2]
            v_i = self.xkN_key[vni]
            v_j = self.xkN_key[vnj]
            if self.diag_Q_R:
                qtarget[_t, v_i] = 1 / cov_dict[vni, vnj, _t]
            else:
                qtarget[_t, v_i, v_j] = cov_dict[vni, vnj, _t]

    def shift_mhe(self):
        """Shifts current initial guesses of variables for the mhe problem"""
        for v in self.lsmhe.component_objects(Var, active=True):
            if type(v.index_set()) == SimpleSet:  #: Don't want simple sets
                break
            else:
                kl = v.keys()
                if len(kl[0]) < 2:
                    break
                for k in kl:
                    if k[0] < self.nfe_t:
                        try:
                            v[k].set_value(v[(k[0] + 1,) + k[1:]])
                        except ValueError:
                            continue

    def load_inputsmhe(self, src, fe=1):
        """Loads inputs into the mhe model"""
        for u in self.u:
            usrc = getattr(src, u)
            utrg = getattr(self.lsmhe, u)
            utrg[fe] = (value(usrc[1]))

    def init_step_mhe(self, tgt, i):
        """Takes the last state-estimate from the mhe to perform an open-loop simulation
        that initializes the last slice of the mhe horizon
        Args:
            tgt (pyomo.core.base.PyomoModel.ConcreteModel): The target"""
        src = self.lsmhe
        for vs in src.component_objects(Var, active=True):
            if vs.getname()[-4:] == "_mhe":
                continue
            vd = getattr(tgt, vs.getname())
            # there are two cases: 1 key 1 elem, several keys 1 element
            vskeys = vs.keys()
            if len(vskeys) == 1:
                #: One key
                for ks in vskeys:
                    for v in vd.itervalues():
                        v.set_value(value(vs[ks]))
            else:
                k = 0
                for ks in vskeys:
                    if k == 0:
                        if type(ks) != tuple:
                            #: Several keys of 1 element each!!
                            vd[1].set_value(value(vs[vskeys[-1]]))  #: This has got to be true
                            break
                        k += 1
                    kj = ks[2:]
                    if vs.getname() in self.states:  #: States start at 0
                        for j in range(0, self.ncp_t + 1):
                            vd[(1, j) + kj].set_value(value(vs[(i, j) + kj]))
                    else:
                        for j in range(1, self.ncp_t + 1):
                            vd[(1, j) + kj].set_value(value(vs[(i, j) + kj]))
        for u in self.u:
            usrc = getattr(src, u)
            utgt = getattr(tgt, u)
            utgt[1] = (value(usrc[i]))
        for x in self.states:
            pn = x + "_ic"
            p = getattr(tgt, pn)
            vs = getattr(self.lsmhe, x)
            for ks in p.iterkeys():
                p[ks].value = value(vs[(i, self.ncp_t) + (ks,)])

    def create_rh_sfx(self):
        """Creates relevant suffixes for K_Matrix (prior at fe=2)
        Args:
            None
        Returns:
            None
        """
        # Degree of freedom variable
        self.lsmhe.dof_v = Suffix(direction=Suffix.EXPORT)
        self.lsmhe.rh_name = Suffix(direction=Suffix.IMPORT)
        # self.lsmhe.ipopt_zL_out = Suffix(direction=Suffix.IMPORT)
        # self.lsmhe.ipopt_zU_out = Suffix(direction=Suffix.IMPORT)
        # self.lsmhe.ipopt_zL_in = Suffix(direction=Suffix.EXPORT)
        # self.lsmhe.ipopt_zU_in = Suffix(direction=Suffix.EXPORT)

        for key in self.x_noisy:
            var = getattr(self.lsmhe, key)
            for j in self.x_vars[key]:
                var[(2, 0) + j].set_suffix_value(self.lsmhe.dof_v, 1)

    def check_active_bound_noisy(self):
        """Checks if the dof_(super-basic) have active bounds, if so, add them to the exclusion list"""
        self.xkN_nexcl = []
        k = 0
        for x in self.x_noisy:
            v = getattr(self.lsmhe, x)
            for j in self.x_vars[x]:
                if v[(2, 0) + j].value - v[(2, 0) + j].lb < 1e-08 or v[(2, 0) + j].ub - v[(2, 0) + j].value < 1e-08:
                    print("Active bound {:s}, {:d}, value {:f}".format(x, j[0], v[(2, 0) + j].value))
                    v[(2, 0) + j].set_suffix_value(self.lsmhe.dof_v, 0)
                    self.xkN_nexcl.append(0)
                    k += 1
                else:
                    v[(2, 0) + j].set_suffix_value(self.lsmhe.dof_v, 1)
                    self.xkN_nexcl.append(1)  #: Not active, add it to the non-exclusion list.
        if k > 0:
            print("I[[check_active_bound_noisy]] {:d} Active bounds.".format(k))

    def deact_icc_mhe(self):
        """Deactivates the icc constraints in the mhe problem"""
        if self.deact_ics:
            for i in self.states:
                icccon = getattr(self.lsmhe, i + "_icc")
                icccon.deactivate()
        #: Maybe only for a subset of the states
        else:
            for i in self.states:
                if i in self.x_noisy:
                    ic_con = getattr(self.lsmhe, i + "_icc")
                    for k in ic_con.keys():
                        if k[2:] in self.x_vars[i]:
                            ic_con[k].deactivate()

    def regen_objective_fun(self):
        """Given the exclusion list, regenerate the expression for the arrival cost"""
        self.lsmhe.Arrival_e_mhe.set_value(0.5 * sum((self.xkN_l[j] - self.lsmhe.x_0_mhe[j]) *
                                                     sum(self.lsmhe.PikN_mhe[j, k] *
                                                         (self.xkN_l[k] - self.lsmhe.x_0_mhe[k]) for k in
                                                         self.lsmhe.xkNk_mhe if self.xkN_nexcl[k])
                                                     for j in self.lsmhe.xkNk_mhe if self.xkN_nexcl[j]))
        self.lsmhe.obfun_mhe.set_value(self.lsmhe.Arrival_e_mhe + self.lsmhe.Q_e_mhe + self.lsmhe.R_e_mhe)
        if self.lsmhe.obfun_dum_mhe.active:
            self.lsmhe.obfun_dum_mhe.deactivate()
        if not self.lsmhe.obfun_mhe.active:
            self.lsmhe.obfun_mhe.activate()

    def load_covariance_prior(self):
        """Computes the reduced-hessian (inverse of the prior-covariance)
        Reads the result_hessian.txt file that contains the covariance information"""
        self.k_aug.options["eig_rh"] = ""
        self.k_aug.solve(self.lsmhe, tee=True)
        self._PI.clear()
        with open("inv_.txt", "r") as rh:
            ll = []
            l = rh.readlines()
            row = 0
            for i in l:
                ll = i.split()
                col = 0
                for j in ll:
                    self._PI[row, col] = float(j)
                    col += 1
                row += 1
            rh.close()
        print("-" * 120)
        print("I[[load covariance]] e-states nrows {:d} ncols {:d}".format(len(l), len(ll)))
        print("-" * 120)

    def set_state_covariance(self):
        """Sets covariance(inverse) for the prior_state.
        Args:
            None
        Return:
            None
        """
        pikn = getattr(self.lsmhe, "PikN_mhe")
        for key_j in self.x_noisy:
            for key_k in self.x_noisy:
                vj = getattr(self.lsmhe, key_j)
                vk = getattr(self.lsmhe, key_k)
                for j in self.x_vars[key_j]:
                    if vj[(2, 0) + j].get_suffix_value(self.lsmhe.dof_v) == 0:
                        #: This state is at its bound, skip
                        continue
                    for k in self.x_vars[key_k]:
                        if vk[(2, 0) + k].get_suffix_value(self.lsmhe.dof_v) == 0:
                            #: This state is at its bound, skip
                            print("vj {:s} {:d} .sfx={:d}, vk {:s} {:d}.sfx={:d}"
                                  .format(key_j, j[0], vj[(2, 0) + j].get_suffix_value(self.lsmhe.dof_v),
                                          key_k, k[0], vk[(2, 0) + k].get_suffix_value(self.lsmhe.dof_v),))
                            continue
                        row = vj[(2, 0) + j].get_suffix_value(self.lsmhe.rh_name)
                        col = vk[(2, 0) + k].get_suffix_value(self.lsmhe.rh_name)
                        #: Ampl does not give you back 0's
                        if not row:
                            row = 0
                        if not col:
                            col = 0

                        # print((row, col), (key_j, j), (key_k, k))
                        q0j = self.xkN_key[key_j, j]
                        q0k = self.xkN_key[key_k, k]
                        pi = self._PI[row, col]
                        try:
                            pikn[q0j, q0k] = pi
                        except KeyError:
                            errk = key_j + "_" + str(j) + ", " + key_k + "_" + str(k)
                            print("Kerror, var {:}".format(errk))
                            pikn[q0j, q0k] = 0.0

    def set_prior_state_from_prior_mhe(self):
        """Mechanism to assign a value to x0 (prior-state) from the previous mhe
        Args:
            None
        Returns:
            None
        """
        for x in self.x_noisy:
            var = getattr(self.lsmhe, x)
            for j in self.x_vars[x]:
                z0dest = getattr(self.lsmhe, "x_0_mhe")
                z0 = self.xkN_key[x, j]
                z0dest[z0] = value(var[(2, 0,) + j])

    def introduce_noise_meas(self, mod, cov_dict):
        self.journalizer("I", self._c_it, "introduce_noise_meas", "Noise introduction")
        # f = open("m0.txt", "w")
        # f1 = open("m1.txt", "w")
        for y in self.y:
            vy = getattr(mod,  y)
            # vy.display(ostream=f)
            for j in self.y_vars[y]:
                vv = value(vy[(1, self.ncp_t) + j])
                sigma = cov_dict[(y, j), (y, j), 1]
                noise = np.random.normal(0, sigma)
                vv += noise
                vy[(1, self.ncp_t) + j].set_value(vv)
            # vy.display(ostream=f1)
        # f.close()
        # f1.close()

    def print_r_mhe(self):
        self.journalizer("I", self._c_it, "print_r_mhe", "res")
        for x in self.x_noisy:
            self.s_estimate[x] = []


        self.ccl.append(value(self.d1.c_capture[1, self.ncp_t]))
        self.sp.append(value(self.ss2.c_capture[1, 1]))
        self.iput.append([value(self.d1.per_opening2[1]), value(self.d1.per_opening1[1])])
        with open("results_mhe.txt", "w") as f:
            for i in range(0, len(self.ccl)):
                c = []
                o = str(self.ccl[i])
                f.write(o)
                for j in range(0, len(self.iput[i])):
                    c.append(str(self.iput[i][j]))
                    f.write("\t" + c[j])
                f.write("\t" + str(self.sp[i]))
                f.write("\t" + str(self._ipt_list[i]))
                for j in range(0, len(self._kt_list[i])):
                    f.write("\t" + self._kt_list[i][j])
                f.write("\t" + self._dt_list[i])
                f.write("\n")
            f.close()