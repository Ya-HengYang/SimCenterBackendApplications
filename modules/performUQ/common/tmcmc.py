"""Implementation of the Transitional Markov Chain Monte Carlo (TMCMC) algorithm."""

import math

import numpy as np
from scipy.optimize import root_scalar
from scipy.special import logsumexp


def _calculate_weights_warm_start(
    beta, current_loglikelihoods, previous_loglikelihoods
):
    """
    Calculate the weights for the warm start stage.

    Args:
        beta (float): The current beta value.
        current_loglikelihoods (np.ndarray): The current log-likelihood values.
        previous_loglikelihoods (np.ndarray): The previous log-likelihood values.

    Returns
    -------
        np.ndarray: The calculated weights.
    """
    log_weights = beta * (current_loglikelihoods - previous_loglikelihoods)
    normalized_log_weights = log_weights - np.max(log_weights)
    normalized_weights = np.exp(normalized_log_weights)
    weights = normalized_weights / np.sum(normalized_weights)
    return weights  # noqa: RET504


def calculate_warm_start_stage(
    current_loglikelihood_approximation, previous_results, threshold_cov=1
):
    """
    Calculate the warm start stage number based on the coefficient of variation of weights.

    Args:
        current_loglikelihood_approximation (callable): Function to approximate current log-likelihoods.
        previous_results (tuple): The previous results containing samples, betas, and log-likelihoods.
        threshold_cov (float, optional): The threshold for the coefficient of variation. Defaults to 1.

    Returns
    -------
        int: The stage number for the warm start.
    """
    stage_nums = sorted(previous_results[0].keys(), reverse=True)
    for stage_num in stage_nums:
        current_loglikelihoods = current_loglikelihood_approximation(
            previous_results[0][stage_num]
        )
        previous_loglikelihoods = previous_results[2][stage_num]
        beta = previous_results[1][stage_num]
        weights = _calculate_weights_warm_start(
            beta, current_loglikelihoods, previous_loglikelihoods
        )
        cov_weights = np.nanstd(weights) / np.nanmean(weights)
        if cov_weights < threshold_cov:
            return stage_num
    return 0


def _calculate_weights(beta_increment, log_likelihoods):
    """
    Calculate the weights for the given beta increment and log-likelihoods.

    Args:
        beta_increment (float): The increment in beta.
        log_likelihoods (np.ndarray): The log-likelihood values.

    Returns
    -------
        np.ndarray: The calculated weights.
    """
    log_weights = beta_increment * log_likelihoods
    normalized_log_weights = log_weights - np.max(log_weights)
    normalized_weights = np.exp(normalized_log_weights)
    weights = normalized_weights / np.sum(normalized_weights)
    return weights  # noqa: RET504


def _calculate_log_evidence(beta_increment, log_likelihoods):
    """
    Calculate the log evidence for the given beta increment and log-likelihoods.

    Args:
        beta_increment (float): The increment in beta.
        log_likelihoods (np.ndarray): The log-likelihood values.

    Returns
    -------
        float: The calculated log evidence.
    """
    log_evidence = logsumexp(beta_increment * log_likelihoods) - np.log(
        len(log_likelihoods)
    )
    return log_evidence  # noqa: RET504


def _increment_beta(log_likelihoods, beta, threshold_cov=1):
    """
    Attempt to increment beta using optimization. If optimization fails, fall back to trial-and-error.

    Args:
        log_likelihoods (np.ndarray): The log-likelihood values.
        beta (float): The current beta value.
        threshold_cov (float, optional): The threshold for the coefficient of variation. Defaults to 1.

    Returns
    -------
        float: The new beta value.
    """

    def cov_objective(beta_increment):
        weights = _calculate_weights(beta_increment, log_likelihoods)
        cov_weights = np.nanstd(weights) / np.nanmean(weights)
        return cov_weights - threshold_cov

    # print(f'{cov_objective(0) = }, {cov_objective(1 - beta) = }')
    # Check if optimization method is feasible
    if np.sign(cov_objective(0)) == np.sign(cov_objective(1 - beta)):
        # If signs are the same, set beta to its maximum possible value of 1
        # print('Optimization not feasible. Setting beta to 1.0')
        # wts = _calculate_weights(1 - beta, log_likelihoods)
        # print(f'cov_weights at new beta = {np.nanstd(wts) / np.nanmean(wts)}')
        return 1.0

    # Try optimization method first
    result = root_scalar(cov_objective, bracket=[0, 1 - beta], method='bisect')

    if result.converged:
        # If optimization succeeds, calculate the new beta
        new_beta = min(beta + result.root, 1)
        # wts = _calculate_weights(result.root, log_likelihoods)
        # print(f'cov_weights at new beta = {np.nanstd(wts) / np.nanmean(wts)}')
        return new_beta  # noqa: RET504

    # Fallback to trial-and-error approach if optimization fails
    # print('Optimization failed. Fallback to trial-and-error approach.')
    beta_increment = 1 - beta
    weights = _calculate_weights(beta_increment, log_likelihoods)
    cov_weights = np.nanstd(weights) / np.nanmean(weights)

    while cov_weights > threshold_cov:
        beta_increment = 0.99 * beta_increment
        weights = _calculate_weights(beta_increment, log_likelihoods)
        cov_weights = np.nanstd(weights) / np.nanmean(weights)

    proposed_beta = beta + beta_increment
    new_beta = min(proposed_beta, 1)
    return new_beta  # noqa: RET504


def _get_scaled_proposal_covariance(samples, weights, scale_factor=0.2):
    """
    Calculate the scaled proposal covariance matrix.

    Args:
        samples (np.ndarray): The samples.
        weights (np.ndarray): The weights.
        scale_factor (float, optional): The scale factor. Defaults to 0.2.

    Returns
    -------
        np.ndarray: The scaled proposal covariance matrix.
    """
    return scale_factor**2 * np.cov(
        samples, rowvar=False, aweights=weights.flatten()
    )


class TMCMC:
    """
    A class to perform Transitional Markov Chain Monte Carlo (TMCMC) sampling.

    Attributes
    ----------
        _log_likelihood_approximation (callable): Function to approximate log-likelihoods.
        _log_posterior_approximation (callable): Function to approximate log-posterior densities.
        num_steps (int): The number of steps for each stage.
        threshold_cov (float): The threshold for the coefficient of variation.
        thinning_factor (int): The thinning factor for the MCMC chain.
        adapt_frequency (int): The frequency of adaptation for the proposal distribution.
    """

    def __init__(
        self,
        log_likelihood_approximation_function,
        log_target_density_approximation_function,
        threshold_cov=1,
        num_steps=1,
        thinning_factor=10,
        adapt_frequency=50,
    ):
        """
        Initialize the TMCMC class.

        Args:
            log_likelihood_approximation_function (callable): Function to approximate log-likelihoods.
            log_target_density_approximation_function (callable): Function to approximate log-posterior densities.
            threshold_cov (float, optional): The threshold for the coefficient of variation. Defaults to 1.
            num_steps (int, optional): The number of steps for each stage. Defaults to 1.
            thinning_factor (int, optional): The thinning factor for the MCMC chain. Defaults to 10.
            adapt_frequency (int, optional): The frequency of adaptation for the proposal distribution. Defaults to 100.
        """
        self._log_likelihood_approximation = log_likelihood_approximation_function
        self._log_posterior_approximation = log_target_density_approximation_function

        self.num_steps = num_steps
        self.threshold_cov = threshold_cov
        self.thinning_factor = thinning_factor
        self.adapt_frequency = adapt_frequency

    @staticmethod
    def _generate_error_message(size):
        return f'Expected a single value, but got {size} values.'

    def _run_one_stage(  # noqa: C901
        self,
        samples,
        log_likelihoods,
        log_target_density_values,
        beta,
        rng,
        log_likelihood_function,
        log_target_density_function,
        scale_factor,
        target_acceptance_rate,
        do_thinning=False,  # noqa: FBT002
        burn_in_steps=0,
    ):
        """
        Run one stage of the TMCMC algorithm.

        Args:
            samples (np.ndarray): The samples.
            log_likelihoods (np.ndarray): The log-likelihood values.
            log_target_density_values (np.ndarray): The log-target density values.
            beta (float): The current beta value.
            rng (np.random.Generator): The random number generator.
            log_likelihood_function (callable): Function to calculate log-likelihoods.
            log_target_density_function (callable): Function to calculate log-target densities.
            scale_factor (float): The scale factor for the proposal distribution.
            target_acceptance_rate (float): The target acceptance rate for the MCMC chain.
            do_thinning (bool, optional): Whether to perform thinning. Defaults to False.
            burn_in_steps (int, optional): The number of burn-in steps. Defaults to 0.

        Returns
        -------
            tuple: A tuple containing the new samples, new log-likelihoods, new log-target density values, new beta, and log evidence.
        """
        # new_beta = _increment_beta(log_likelihoods, beta)
        new_beta = _increment_beta(log_likelihoods, beta)
        log_evidence = _calculate_log_evidence(new_beta - beta, log_likelihoods)
        weights = _calculate_weights(new_beta - beta, log_likelihoods)

        proposal_covariance = _get_scaled_proposal_covariance(
            samples, weights, scale_factor
        )
        try:
            cholesky_lower_triangular_matrix = np.linalg.cholesky(
                proposal_covariance
            )
        except np.linalg.LinAlgError as exc:
            msg = f'Cholesky decomposition failed: {exc}'
            raise RuntimeError(msg) from exc

        new_samples = np.zeros_like(samples)
        new_log_likelihoods = np.zeros_like(log_likelihoods)
        new_log_target_density_values = np.zeros_like(log_target_density_values)

        current_samples = samples.copy()
        current_log_likelihoods = log_likelihoods.copy()
        current_log_target_density_values = log_target_density_values.copy()

        num_samples = samples.shape[0]
        num_accepts = 0
        n_adapt = 1
        step_count = 0
        num_steps = self.num_steps
        # print(f'{new_beta = }, {do_thinning = }, {num_steps = }')
        for k in range(burn_in_steps + num_samples):
            index = rng.choice(num_samples, p=weights.flatten())
            if k >= burn_in_steps:
                if new_beta == 1 or do_thinning:
                    num_steps = self.num_steps * self.thinning_factor
            # print(f'{new_beta = }, {do_thinning = }, {num_steps = }')
            for _ in range(num_steps):
                step_count += 1
                if step_count % self.adapt_frequency == 0:
                    acceptance_rate = num_accepts / self.adapt_frequency
                    num_accepts = 0
                    n_adapt += 1
                    ca = (acceptance_rate - target_acceptance_rate) / (
                        math.sqrt(n_adapt)
                    )
                    scale_factor = scale_factor * np.exp(ca)
                    proposal_covariance = _get_scaled_proposal_covariance(
                        current_samples, weights, scale_factor
                    )
                    cholesky_lower_triangular_matrix = np.linalg.cholesky(
                        proposal_covariance
                    )

                standard_normal_samples = rng.standard_normal(
                    size=current_samples.shape[1]
                )
                proposed_state = (
                    current_samples[index, :]
                    + cholesky_lower_triangular_matrix @ standard_normal_samples
                ).reshape(1, -1)

                log_likelihood_at_proposed_state = log_likelihood_function(
                    proposed_state
                )
                log_target_density_at_proposed_state = log_target_density_function(
                    proposed_state, log_likelihood_at_proposed_state
                )
                log_hastings_ratio = (
                    log_target_density_at_proposed_state
                    - current_log_target_density_values[index]
                )
                u = rng.uniform()
                accept = np.log(u) <= log_hastings_ratio
                if accept:
                    num_accepts += 1
                    current_samples[index, :] = proposed_state
                    # current_log_likelihoods[index] = log_likelihood_at_proposed_state
                    if log_likelihood_at_proposed_state.size != 1:
                        msg = self._generate_error_message(
                            log_likelihood_at_proposed_state.size
                        )
                        raise ValueError(msg)
                    current_log_likelihoods[index] = (
                        log_likelihood_at_proposed_state.item()
                    )
                    # current_log_target_density_values[index] = (
                    #     log_target_density_at_proposed_state
                    # )
                    if log_target_density_at_proposed_state.size != 1:
                        msg = self._generate_error_message(
                            log_target_density_at_proposed_state.size
                        )
                        raise ValueError(msg)
                    current_log_target_density_values[index] = (
                        log_target_density_at_proposed_state.item()
                    )
                    if k >= burn_in_steps:
                        weights = _calculate_weights(
                            new_beta - beta, current_log_likelihoods
                        )
            if k >= burn_in_steps:
                k_prime = k - burn_in_steps
                new_samples[k_prime, :] = current_samples[index, :]
                new_log_likelihoods[k_prime] = current_log_likelihoods[index]
                new_log_target_density_values[k_prime] = (
                    current_log_target_density_values[index]
                )

        return (
            new_samples,
            new_log_likelihoods,
            new_log_target_density_values,
            new_beta,
            log_evidence,
        )

    def run(
        self,
        samples_dict,
        betas_dict,
        log_likelihoods_dict,
        log_target_density_values_dict,
        log_evidence_dict,
        rng,
        stage_num,
        num_burn_in=0,
    ):
        """
        Run the TMCMC algorithm.

        Args:
            samples_dict (dict): Dictionary of samples for each stage.
            betas_dict (dict): Dictionary of beta values for each stage.
            log_likelihoods_dict (dict): Dictionary of log-likelihood values for each stage.
            log_target_density_values_dict (dict): Dictionary of log-target density values for each stage.
            log_evidence_dict (dict): Dictionary of log evidence values for each stage.
            rng (np.random.Generator): The random number generator.
            stage_num (int): The current stage number.
            num_burn_in (int, optional): The number of burn-in steps. Defaults to 0.

        Returns
        -------
            tuple: A tuple containing the updated dictionaries for samples, betas, log-likelihoods, log-target density values, and log evidence.
        """
        self.num_dimensions = samples_dict[0].shape[1]
        self.target_acceptance_rate = 0.23 + 0.21 / self.num_dimensions
        self.scale_factor = 2.4 / np.sqrt(self.num_dimensions)
        while betas_dict[stage_num] < 1:
            # print(f'Stage {stage_num}')
            # print(f'Beta: {betas_dict[stage_num]}')
            (
                new_samples,
                new_log_likelihoods,
                new_log_target_density_values,
                new_beta,
                log_evidence,
            ) = self._run_one_stage(
                samples_dict[stage_num],
                log_likelihoods_dict[stage_num],
                log_target_density_values_dict[stage_num],
                betas_dict[stage_num],
                rng,
                self._log_likelihood_approximation,
                self._log_posterior_approximation,
                self.scale_factor,
                self.target_acceptance_rate,
                do_thinning=False,
                burn_in_steps=num_burn_in,
            )
            stage_num += 1
            samples_dict[stage_num] = new_samples
            betas_dict[stage_num] = new_beta
            log_likelihoods_dict[stage_num] = new_log_likelihoods
            log_target_density_values_dict[stage_num] = new_log_target_density_values
            log_evidence_dict[stage_num] = log_evidence

        return (
            samples_dict,
            betas_dict,
            log_likelihoods_dict,
            log_target_density_values_dict,
            log_evidence_dict,
        )


if __name__ == '__main__':
    import numpy as np
    from scipy import stats

    # Define log-likelihood function (2D Gaussian)
    def _log_likelihood_approximation_function(samples):
        # Assuming a Gaussian log-likelihood with mean (0, 0) and covariance identity
        return -0.5 * np.sum((samples - 10) ** 2, axis=1)

    # Define log-posterior function (assume same as log-likelihood for this simple case)
    def _log_target_density_approximation_function(samples, log_likelihoods):  # noqa: ARG001
        return log_likelihoods  # In this simple case, they are the same

    # Initialize the TMCMC sampler
    tmcmc_sampler = TMCMC(
        _log_likelihood_approximation_function,
        _log_target_density_approximation_function,
        threshold_cov=1,
        num_steps=1,
        thinning_factor=5,
        adapt_frequency=50,
    )

    # Initial parameters
    num_samples = 2000  # Number of samples
    num_dimensions = 2  # Dimensionality of the target distribution
    rng = np.random.default_rng(42)  # Random number generator

    # Start with some random samples
    initial_samples = rng.normal(size=(num_samples, num_dimensions))
    initial_log_likelihoods = _log_likelihood_approximation_function(initial_samples)
    initial_log_target_density_values = initial_log_likelihoods

    # Dictionaries to store results for each stage
    samples_dict = {0: initial_samples}
    betas_dict = {0: 0.0}  # Start with beta=0 (prior importance)
    log_likelihoods_dict = {0: initial_log_likelihoods}
    log_target_density_values_dict = {0: initial_log_target_density_values}
    log_evidence_dict = {0: 0}

    # Run TMCMC
    stage_num = 0
    (
        samples_dict,
        betas_dict,
        log_likelihoods_dict,
        log_target_density_values_dict,
        log_evidence_dict,
    ) = tmcmc_sampler.run(
        samples_dict,
        betas_dict,
        log_likelihoods_dict,
        log_target_density_values_dict,
        log_evidence_dict,
        rng,
        stage_num,
        num_burn_in=100,
    )

    # Display results
    final_stage_num = max(samples_dict.keys())
    print(  # noqa: T201
        f'Final samples (stage {final_stage_num}): \n{samples_dict[final_stage_num]}'
    )
    print(f'Betas: {betas_dict.values()}')  # noqa: T201
    print(f'Log-evidence values: {log_evidence_dict.values()}')  # noqa: T201
    print(f'Total log-evidence: {sum(log_evidence_dict.values())}')  # noqa: T201
