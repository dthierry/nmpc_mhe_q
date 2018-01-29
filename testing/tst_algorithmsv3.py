#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import print_function
from pyomo.environ import *
from pyomo.core.base import Constraint, Objective, Suffix, minimize
from pyomo.opt import ProblemFormat, SolverFactory
from nmpc_mhe.dync.MHEGenv2 import MheGen
from sample_mods.bfb.nob5_hi_t import bfb_dae
from pyomo.opt import TerminationCondition
from snapshots.snap_shot import snap
import sys, os
import itertools, sys
from numpy.random import normal as npm

""""Testing a simple controller run with snapshots every x iterations"""

def main():
    states = ["Hgc", "Nsc", "Hsc", "Hge", "Nse", "Hse"]
    x_noisy = ["Hgc", "Nsc", "Hsc", "Hge", "Nse", "Hse"]
    u = ["u1"]
    u_bounds = {"u1":(162.183495794 * 0.0005, 162.183495794 * 10000)}
    ref_state = {("c_capture", ((),)): 0.50}

    nfe_mhe = 10
    y = ["Tgb", "vg"]
    nfet = 10
    ncpx = 3
    nfex = 5
    tfe = [i for i in range(1, nfe_mhe + 1)]
    lfe = [i for i in range(1, nfex + 1)]
    lcp = [i for i in range(1, ncpx + 1)]
    lc = ['c', 'h', 'n']

    y_vars = {
        "Tgb": [i for i in itertools.product(lfe, lcp)],
        "vg": [i for i in itertools.product(lfe, lcp)]
        }
    # x_vars = dict()
    x_vars = {
              # "Nge": [i for i in itertools.product(lfe, lcp, lc)],
              # "Hge": [i for i in itertools.product(lfe, lcp)],
              "Nsc": [i for i in itertools.product(lfe, lcp, lc)],
              "Hsc": [i for i in itertools.product(lfe, lcp)],
              "Nse": [i for i in itertools.product(lfe, lcp, lc)],
              "Hse": [i for i in itertools.product(lfe, lcp)],
              "Hgc": [i for i in itertools.product(lfe, lcp)],
              "Hge": [i for i in itertools.product(lfe, lcp)],
              # "mom": [i for i in itertools.product(lfe, lcp)]
              }

    # States -- (5 * 3 + 6) * fe_x * cp_x.
    # For fe_x = 5 and cp_x = 3 we will have 315 differential-states.

    e = MheGen(bfb_dae, 800/nfe_mhe, states, u, x_noisy, x_vars, y, y_vars,
               nfe_tmhe=nfe_mhe, ncp_tmhe=1,
               nfe_tnmpc=nfe_mhe, ncp_tnmpc=1,
               ref_state=ref_state, u_bounds=u_bounds,
               nfe_t=5, ncp_t=1,
               k_aug_executable="/home/dav0/k2/KKT_matrix/src/k_aug/k_aug"
               )

    # 10 fe & _t=1000 definitely degenerate
    # 10 fe & _t=900 definitely degenerate
    # 10 fe & _t=120 sort-of degenerate
    # 10 fe & _t=50 sort-of degenerate
    # 10 fe & _t=50 eventually sort-of degenerate
    # 10 fe & _t=1 eventually sort-of degenerate
    e.load_solfile(e.SteadyRef, "ref_ss.sol")  #: Loads solfile snap
    results = e.ipopt.solve(e.SteadyRef, tee=True, load_solutions=True, report_timing=True)
    if results.solver.termination_condition != TerminationCondition.optimal:
        print("Oh no!")
        sys.exit()

    e.get_state_vars(skip_solve=True)
    e.SteadyRef.report_zL(filename="mult_ss")
    e.load_d_s(e.PlantSample)
    e.PlantSample.create_bounds()
    e.solve_dyn(e.PlantSample)

    q_cov = {}
    for i in tfe:
        for j in itertools.product(lfe, lcp, lc):
            q_cov[("Nse", j), ("Nse", j), i] = 7525.81478168 * 0.005
            q_cov[("Nsc", j), ("Nsc", j), i] = 117.650089456 * 0.005
    #             q_cov[("Nse", j), ("Nse", j), i] = 735.706082714 * 0.005
    for i in tfe:
        for j in itertools.product(lfe, lcp):
            # q_cov[("Hge", j), ("Hge", j), i] = 2194.25390583 * 0.005
            q_cov[("Hse", j), ("Hse", j), i] = 731143.716603 * 0.005
            q_cov[("Hsc", j), ("Hsc", j), i] = 16668.3312216 * 0.005
            q_cov[("Hge", j), ("Hge", j), i] = 2166.86838591 * 0.005
            q_cov[("Hgc", j), ("Hgc", j), i] = 47.7911012193 * 0.005
            # q_cov[("mom", j), ("mom", j), i] = 1.14042251669 * 0.005

    # for i in lfe:
    #     for j in [(1,1, 'c'), (5,3, 'c')]:
    #         m_cov[("yb", j), ("yb", j), i] = 1e-04

    u_cov = {}
    for i in [i for i in range(1, nfe_mhe+1)]:
        u_cov["u1", i] = 162.183495794 * 0.005

    m_cov = {}
    for i in tfe:
        for j in itertools.product(lfe, lcp):
            m_cov[("Tgb", j), ("Tgb", j), i] = 40 * 0.005
            m_cov[("vg", j), ("vg", j), i] = 0.902649386907 * 0.005

    e.set_covariance_meas(m_cov)
    e.set_covariance_disturb(q_cov)
    e.set_covariance_u(u_cov)
    e.create_rh_sfx()  #: Reduced hessian computation

    # dum = e.d_mod(1, 6, _t=e.hi_t)
    # e.PlantSample.display(filename="schwer0.txt")
    # e.load_iguess_single(e.PlantSample, dum, src_fe=1, tgt_fe=1)
    # dum.display(filename="schwer1.txt")
    # e.load_init_state_gen(dum, src_kind="mod", ref=e.PlantSample, fe=1)
    # e.init_step_mhe(dum, e.nfe_t)
    # e.solve_dyn(dum)
    # dum.display(filename="schwer2.txt")
    e.init_lsmhe_prep(e.PlantSample)
    e.shift_mhe()
    e.init_step_mhe()
    e.solve_dyn(e.lsmhe,
                skip_update=False,
                max_cpu_time=600,
                ma57_pre_alloc=5, tag="lsmhe")  #: Pre-loaded mhe solve

    e.check_active_bound_noisy()
    e.load_covariance_prior()
    e.set_state_covariance()

    e.regen_objective_fun()  #: Regen erate the obj fun
    e.deact_icc_mhe()  #: Remove the initial conditions

    e.set_prior_state_from_prior_mhe()  #: Update prior-state
    e.find_target_ss()  #: Compute target-steady state (beforehand)

    #: Create NMPC
    e.create_nmpc()
    e.update_targets_nmpc()
    e.compute_QR_nmpc(n=-1)
    e.new_weights_olnmpc(10000, 1e+06)
    e.solve_dyn(e.PlantSample, stop_if_nopt=True)
    isnap = [i*50 for i in range(1, 25)]
    for i in range(1, 1200):
        if i in isnap:
            keepsolve=True
            wantparams=True
        else:
            keepsolve=False
            wantparams=False

        if i == 400:
            ref_state = {("c_capture", ((),)): 0.63}
            e.change_setpoint(ref_state=ref_state, keepsolve=True, wantparams=True)
            e.compute_QR_nmpc(n=-1)
            e.new_weights_olnmpc(10000, 1e+06)
        elif i == 800:
            ref_state = {("c_capture", ((),)): 0.5}
            e.change_setpoint(ref_state=ref_state, keepsolve=True, wantparams=True)
            e.compute_QR_nmpc(n=-1)
            e.new_weights_olnmpc(10000, 1e+06)

        e.solve_dyn(e.PlantSample, stop_if_nopt=True, tag="plant", keepsolve=keepsolve, wantparams=wantparams)
        e.PlantSample.hi_t.display()
        e.update_state_real()  # update the current state
        e.update_soi_sp_nmpc()
        #
        e.update_noise_meas(m_cov)
        e.load_input_mhe("mod", src=e.PlantSample)  #: The inputs must coincide
        #
        e.patch_meas_mhe(e.PlantSample, noisy=True)  #: Get the measurement
        e.compute_y_offset()
        #
        e.init_step_mhe()  # Initialize next time-slot
        #
        stat = e.solve_dyn(e.lsmhe,
                         skip_update=False, iter_max=500,
                         jacobian_regularization_value=1e-04,
                         max_cpu_time=600, tag="lsmhe", keepsolve=keepsolve, wantparams=wantparams)
        e.lsmhe.write_nl(name="failed_mhe1.nl")
        # e.lsmhe.hi_t.display()
        e.lsmhe.report_zL()
        if stat == 1:
            stat = e.solve_dyn(e.lsmhe,
                             skip_update=True,
                             iter_max=250,
                             stop_if_nopt=True,
                             jacobian_regularization_value=1e-02,
                             linear_scaling_on_demand=True, tag="lsmhe")
            if stat != 0:
                e.lsmhe.write_nl(name="bad_mhe.nl")
                sys.exit()
        e.update_state_mhe()
        # # Prior-Covariance stuff
        e.check_active_bound_noisy()
        e.load_covariance_prior()
        e.set_state_covariance()
        e.regen_objective_fun()
        # # Update prior-state
        e.set_prior_state_from_prior_mhe()
        #
        e.print_r_mhe()
        e.print_r_dyn()
        #
        e.shift_mhe()
        e.shift_measurement_input_mhe()

        e.initialize_olnmpc(e.PlantSample, "estimated")
        e.load_init_state_nmpc(src_kind="state_dict", state_dict="estimated")
        stat_nmpc = e.solve_dyn(e.olnmpc, skip_update=False, max_cpu_time=300,
                                jacobian_regularization_value=1e-04, tag="olnmpc",
                                keepsolve = keepsolve, wantparams = wantparams)

        if stat_nmpc != 0:
            e.olnmpc.write_nl(name="bad.nl")
            e.solve_dyn(e.olnmpc, skip_update=False, max_cpu_time=300, jacobian_regularization_value=1e-04, tag="olnmpc")
            if stat != 0:
                e.lsmhe.write_nl(name="bad_mhe.nl")
                sys.exit()

        e.update_u(e.olnmpc)
        e.print_r_nmpc()
        #
        e.cycleSamPlant(plant_step=True)
        e.plant_uinject(e.PlantSample, src_kind="dict", skip_homotopy=True)
        # e.plant_input_gen(e.PlantSample,  "mod", src=e.ss2)


if __name__ == "__main__":
    main()
