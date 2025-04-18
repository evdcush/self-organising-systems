"""Module containing step_env.

step_env is the recommended way to run a step on an environment. If you need
custom steps, use this as an example.

Copyright 2023 Google LLC

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    https://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

from functools import partial
from typing import Callable, Iterable

from jax import jit
from jax import vmap
import jax.random as jr

from self_organising_systems.biomakerca.agent_logic import AgentLogic
from self_organising_systems.biomakerca.cells_logic import air_cell_op
from self_organising_systems.biomakerca.cells_logic import earth_cell_op
from self_organising_systems.biomakerca.env_logic import AgentProgramType
from self_organising_systems.biomakerca.env_logic import ExclusiveOp
from self_organising_systems.biomakerca.env_logic import balance_soil
from self_organising_systems.biomakerca.env_logic import env_increase_age
from self_organising_systems.biomakerca.env_logic import env_perform_exclusive_update
from self_organising_systems.biomakerca.env_logic import env_perform_reproduce_update
from self_organising_systems.biomakerca.env_logic import env_perform_parallel_update
from self_organising_systems.biomakerca.env_logic import env_process_gravity
from self_organising_systems.biomakerca.env_logic import EnvTypeType
from self_organising_systems.biomakerca.env_logic import intercept_reproduce_ops
from self_organising_systems.biomakerca.env_logic import KeyType
from self_organising_systems.biomakerca.env_logic import PerceivedData
from self_organising_systems.biomakerca.env_logic import process_energy
from self_organising_systems.biomakerca.env_logic import process_structural_integrity_n_times
from self_organising_systems.biomakerca.environments import EnvConfig
from self_organising_systems.biomakerca.environments import Environment
from self_organising_systems.biomakerca.mutators import Mutator, SexualMutator



@partial(jit, static_argnames=[
    "config", "agent_logic", "excl_fs", "do_reproduction",
    "enable_asexual_reproduction", "enable_sexual_reproduction",
    "does_sex_matter", "mutate_programs", "mutator", "sexual_mutator",
    "intercept_reproduction", "n_sparse_max", "return_metrics"])
def step_env(
    key: KeyType, env: Environment, config: EnvConfig,
    agent_logic: AgentLogic,
    programs: AgentProgramType,
    excl_fs: Iterable[
        tuple[EnvTypeType, Callable[[KeyType, PerceivedData], ExclusiveOp]]]
    = None,
    do_reproduction=True,
    enable_asexual_reproduction=True,
    enable_sexual_reproduction=False,
    does_sex_matter=True,
    mutate_programs=False,
    mutator: (Mutator | None) = None,
    sexual_mutator: (SexualMutator | None) = None,
    intercept_reproduction=False,
    min_repr_energy_requirement=None,
    n_sparse_max: (int | None ) = None,
    return_metrics=False):
  """Perform one step for the environment.
  
  There are several different settings for performing a step. The most important
  is likely whether and how to setup reproduction. Read the Arguments section
  for more details.
  
  Arguments:
    key: jax random number generator value.
    env: the input environment to modify.
    config: the laws of the physics of the environment.
    agent_logic: the architecture used by agents to perform parallel,
      exclusive and reproduce operations.
    programs: the parameters used by agent_logic. Must be a line for each
      agent_id.
    excl_fs: the exclusive logic of materials. Defaults to AIR spreading
      through VOID, and EARTH acting like falling-sand.
    do_reproduction: whether reproduction is enabled.
    enable_asexual_reproduction: if set to True, asexual reproduction is
      enabled. if do_reproduction is enabled, at least one of
      enable_asexual_reproduction and enable_sexual_reproduction must be True.
    enable_sexual_reproduction: if set to True, sexual reproduction is enabled.
    does_sex_matter: if set to True, and enable_sexual_reproduction is True,
      then sexual reproduction can only occur between different sex entities.
    mutate_programs: relevant only if do_reproduction==True. In that case,
      determines whether reproduction is performed with or without mutation.
      Beware! Reproduction *without* mutation creates agents with identical
      agent_ids! This may be a problem if you don't want newborns to exchange
      nutrients with their parents.
      If set to true, 'mutator' must be a valid Mutator class.
    mutator: relevant only if we reproduce with mutation. In that case,
      mutator determines how to extract parameters and how to modify them.
    sexual_mutator: relevant only if we reproduce with mutation. In that case,
      it determines how to perform sexual reproduction.
    intercept_reproduction: useful for petri-dish-like experiments. If set to
      true, whenever a ReproduceOp triggers, instead of creating a new seed, we
      simply destroy the flower and record its occurrence. We consider it a
      successful reproduction if the energy in the seed would be
      >= min_repr_energy_requirement (which must be set!). If set to true, 
      this function returns also the counter of successful reproductions 
      intercepted.
    min_repr_energy_requirement: relevant only if intercepting reproductions.
      Determines whether the intercepted seed would have had enough energy to
      count as a successful reproduction.
    n_sparse_max: either an int or None. If set to int, we will use a budget for
      the amounts of agent operations allowed at each step.
    return_metrics: if True, returns reproduction metrics. May be extended to
      return more metrics in the future.
  Returns:
    an updated environment. If intercept_reproduction is True, returns also the
    number of successful reproductions intercepted.
  """
  assert (not do_reproduction or
          (enable_asexual_reproduction or enable_sexual_reproduction))
  etd = config.etd
  if excl_fs is None:
    excl_fs = ((etd.types.AIR, air_cell_op), (etd.types.EARTH, earth_cell_op))

  if mutate_programs:
    agent_params, _ = vmap(mutator.split_params)(programs)
  else:
    agent_params = programs
  par_programs, excl_programs, repr_programs = agent_logic.split_params_f(
      agent_params)

  if config.soil_unbalance_limit > 0:
    ku, key = jr.split(key)
    env = balance_soil(ku, env, config)

  # do a few steps of structural integrity:
  env = process_structural_integrity_n_times(env, config, 5)

  env = env_process_gravity(env, etd)

  # doing reproduction here to actually show the flowers at least for one step.
  if do_reproduction:
    repr_f = agent_logic.repr_f
    if intercept_reproduction:
      # Reproduction just destroys flowers. but we keep track of 'successful' 
      # reproductions and return that as an extra value.
      # this is useful only for evolving plants outside of a full environment.
      ku, key = jr.split(key)
      env, n_successful_repr = intercept_reproduce_ops(
          ku, env, repr_programs, config, repr_f, min_repr_energy_requirement)
    else:
      ku, key = jr.split(key)
      if mutate_programs:
        # metrics are supported only here.
        repr_result = env_perform_reproduce_update(
            ku, env, repr_programs, config, repr_f, mutate_programs, programs,
            mutator.mutate, enable_asexual_reproduction,
            enable_sexual_reproduction, does_sex_matter, sexual_mutator.mutate,
            mutator.split_params, agent_logic.get_sex, n_sparse_max,
            return_metrics)
        if return_metrics:
          (env, programs), metrics = repr_result
        else:
          env, programs = repr_result
      else:
        env = env_perform_reproduce_update(
            ku, env, repr_programs, config, repr_f, n_sparse_max=n_sparse_max)

  # parallel updates
  k1, key = jr.split(key)
  env = env_perform_parallel_update(
      k1, env, par_programs, config, agent_logic.par_f, n_sparse_max)

  # energy absorbed and generated by materials.
  env = process_energy(env, config)

  # exclusive updates
  k1, key = jr.split(key)
  env = env_perform_exclusive_update(
      k1, env, excl_programs, config, excl_fs, agent_logic.excl_f, n_sparse_max)

  # increase age.
  env = env_increase_age(env, etd)

  rval = (env, programs) if mutate_programs else env
  rval = (rval, n_successful_repr) if intercept_reproduction else rval
  rval = (rval, metrics) if return_metrics else rval
  return rval
