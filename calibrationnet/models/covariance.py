import math
from typing import Optional


class CovarianceMixin:
    """Correlation calculation shared by every fit-result table.

    Works on any model that stores:
      - "var_names": the varied parameter names used in the fit, in the order
        they were added to lmfit, and they are the row/column labels of the covariance matrix,
        which is a bare grid of numbers with no labels of its own;
      - "covariance": the covariance matrix over the varied parameters, in that order,
        as a nested list (JSONB).

    The correlations are deliberately NOT stored anywhere: they are a pure
    function of the covariance matrix (corr_ij = cov_ij / sqrt(cov_ii*cov_jj),
    verified digit-for-digit identical to lmfit's .correl), so this mixin
    derives them on demand and stored data can never disagree with itself.
    See docs/fit_storage.md for a worked example.
    """

    def correlations(self, name: Optional[str] = None):
        """Parameter correlations, derived from the stored covariance matrix.

        With a name, returns {other parameter name: correlation} exactly as lmfit's
        params[name].correl reports. Without a name, returns the full symmetric matrix as
        {name: {other: correlation}}.
        """
        if not self.covariance or not self.var_names:
            return None

        sd = [math.sqrt(self.covariance[i][i]) for i in range(len(self.var_names))]

        def row(i):
            return {
                other: self.covariance[i][j] / (sd[i] * sd[j]) for j, other in enumerate(self.var_names) if j != i
                }

        if name is not None:
            return row(self.var_names.index(name))
        else:
            return {n: row(i) for i, n in enumerate(self.var_names)}
