import numpy as np
import pandas as pd
from six import string_types
import scipy.ndimage as ndi
import scipy.stats as st
from scipy.optimize import curve_fit

from .stimulus_analysis import StimulusAnalysis, get_fr


class DriftingGratings(StimulusAnalysis):
    def __init__(self, ecephys_session, **kwargs):
        super(DriftingGratings, self).__init__(ecephys_session, **kwargs)

        self._orivals = None
        self._number_ori = None
        self._tfvals = None
        self._number_tf = None
        self._response_events = None
        self._response_trials = None

    PEAK_COLS = [('cell_specimen_id', np.uint64), ('pref_ori_dg', np.float64), ('pref_tf_dg', np.float64),
                 ('num_pref_trials_dg', np.uint64), ('responsive_dg', bool), ('g_osi_dg', np.float64),
                 ('g_dsi_dg', np.float64), ('tfdi_dg', np.float64), ('reliability_dg', np.float64),
                 ('lifetime_sparseness_dg', np.float64), ('fit_tf_dg', np.float64), ('fit_tf_ind_dg', np.float64),
                 ('tf_low_cutoff_dg', np.float64), ('tf_high_cutoff_dg', np.float64), ('run_pval_dg', np.float64),
                 ('run_resp_dg', np.float64), ('stat_resp_dg', np.float64), ('run_mod_dg', np.float64),
                 ('peak_blank_dg', np.float64), ('all_blank_dg', np.float64)]

    @property
    def peak_columns(self):
        return [c[0] for c in self.PEAK_COLS]

    @property
    def peak_dtypes(self):
        return [c[1] for c in self.PEAK_COLS]

    @property
    def orivals(self):
        if self._orivals is None:
            self._get_stim_table_stats()

        return self._orivals

    @property
    def number_ori(self):
        if self._number_ori is None:
            self._get_stim_table_stats()

        return self._number_ori

    @property
    def tfvals(self):
        if self._tfvals is None:
            self._get_stim_table_stats()

        return self._tfvals

    @property
    def number_tf(self):
        if self._tfvals is None:
            self._get_stim_table_stats()

        return self._number_tf

    @property
    def stim_table(self):
        # Stimulus table is already in EcephysSession object, just need to subselect 'static_gratings' presentations.
        if self._stim_table is None:
            # TODO: Give warning if no stimulus
            if self._stimulus_names is None:
                # Older versions of NWB files the stimulus name is in the form stimulus_gratings_N, so if
                # self._stimulus_names is not explicity specified try to figure out stimulus
                stims_table = self.ecephys_session.stimulus_presentations
                stim_names = [s for s in stims_table['stimulus_name'].unique()
                              if s.lower().startswith('drifting_gratings')]

                self._stim_table = stims_table[stims_table['stimulus_name'].isin(stim_names)]

            else:
                self._stimulus_names = [self._stimulus_names] if isinstance(self._stimulus_names, string_types) \
                    else self._stimulus_names
                self._stim_table = self.ecephys_session.get_presentations_for_stimulus(self._stimulus_names)

        return self._stim_table

    @property
    def mean_sweep_events(self):
        if self._mean_sweep_events is None:
            # TODO: Should dtype for matrix be uint?
            self._mean_sweep_events = self.sweep_events.applymap(do_sweep_mean)

        return self._mean_sweep_events

    @property
    def sweep_p_values(self):
        if self._sweep_p_values is None:
            self._sweep_p_values = self.calc_sweep_p_values(offset=2.0)

        return self._sweep_p_values

    @property
    def response_events(self):
        if self._response_events is None:
            self._get_response_events()

        return self._response_events

    @property
    def response_trials(self):
        if self._response_trials is None:
            self._get_response_events()

        return self._response_trials

    @property
    def peak(self):
        if self._peak is None:
            peak_df = pd.DataFrame(np.empty(self.numbercells, dtype=np.dtype(self.PEAK_COLS)),
                                   index=range(self.numbercells))

            peak_df['fit_tf_ind_dg'] = np.nan
            peak_df['fit_tf_dg'] = np.nan
            peak_df['tf_low_cutoff_dg'] = np.nan
            peak_df['tf_high_cutoff_dg'] = np.nan

            peak_df['lifetime_sparseness_dg'] = self._get_lifetime_sparseness()
            peak_df['cell_specimen_id'] = self.spikes.keys()
            for nc, unit_id in enumerate(self.spikes.keys()):
                peaks = np.where(self.response_events[:, 1:, nc, 0] == self.response_events[:, 1:, nc, 0].max())
                pref_ori = peaks[0][0]
                pref_tf = peaks[1][0]



                stim_table_mask = (self.stim_table['TF'] == self.tfvals[pref_tf]) & \
                                  (self.stim_table['Ori'] == self.orivals[pref_ori])

                peak_df.loc[nc, 'pref_ori_dg'] = self.orivals[pref_ori]
                peak_df.loc[nc, 'pref_tf_dg'] = self.tfvals[pref_tf]
                peak_df.loc[nc, 'num_pref_trials_dg'] = self.response_events[pref_ori, pref_tf + 1, nc, 2]
                peak_df.loc[nc, 'responsive_dg'] = self.response_events[pref_ori, pref_tf + 1, nc, 2] > 3
                peak_df.loc[nc, ['g_osi_dg', 'g_dsi_dg']] = self._get_osi(pref_tf, nc)
                peak_df.loc[nc, 'reliability_dg'] = self._get_reliability(unit_id, stim_table_mask)
                peak_df.loc[nc, 'tfdi_dg'] = self._get_tfdi(pref_ori, nc)
                peak_df.loc[nc, ['run_pval_dg', 'run_mod_dg', 'run_resp_dg', 'stat_resp_dg']] = \
                    self._get_running_modulation(pref_ori, pref_tf, unit_id)
                peak_df.loc[nc, ['peak_blank_dg', 'all_blank_dg']] = self._get_suppressed_contrast(pref_ori, pref_tf,
                                                                                                   nc)
                if self.response_events[pref_ori, pref_tf + 1, nc, 2] > 3:
                    peak_df.loc[nc, ['fit_tf_ind_dg', 'fit_tf_dg', 'tf_low_cutoff_dg', 'tf_high_cutoff_dg']] = \
                        self._fit_tf_tuning(pref_ori, pref_tf, nc)

            self._peak = peak_df

        return self._peak

    def _get_lifetime_sparseness(self):
        """Computes lifetime sparseness of responses for all cells

        :return:
        """
        response = self.response_events[:, 1:, :, 0].reshape(40, self.numbercells)
        return (1-(1/40.)*((np.power(response.sum(axis=0), 2))/(np.power(response, 2).sum(axis=0))))/(1-(1/40.))

    def _get_response_events(self):
        response_events = np.empty((self.number_ori, self.number_tf+1, self.numbercells, 3))
        response_events[:] = np.NaN

        blank = self.mean_sweep_events[np.isnan(self.stim_table['Ori'])]
        response_trials = np.empty((self.number_ori, self.number_tf+1, self.numbercells, len(blank)))
        response_trials[:] = np.NaN

        response_events[0, 0, :, 0] = blank.mean(axis=0)
        response_events[0, 0, :, 1] = blank.std(axis=0) / np.sqrt(len(blank))
        blank_p = self.sweep_p_values[np.isnan(self.stim_table['Ori'])]
        response_events[0, 0, :, 2] = blank_p[blank_p < 0.05].count().values
        response_trials[0, 0, :, :] = blank.values.T

        for oi, ori in enumerate(self.orivals):
            ori_mask = self.stim_table['Ori'] == ori
            for ti, tf in enumerate(self.tfvals):
                mask = ori_mask & (self.stim_table['TF'] == tf)
                subset = self.mean_sweep_events[mask]
                subset_p = self.sweep_p_values[mask]
                response_events[oi, ti + 1, :, 0] = subset.mean(axis=0)
                response_events[oi, ti + 1, :, 1] = subset.std(axis=0) / np.sqrt(len(subset))
                response_events[oi, ti + 1, :, 2] = subset_p[subset_p < 0.05].count().values
                response_trials[oi, ti + 1, :, :subset.shape[0]] = subset.values.T

        self._response_events = response_events
        self._response_trials = response_trials

    def _get_stim_table_stats(self):
        self._orivals = np.sort(self.stim_table['Ori'].dropna().unique())
        self._number_ori = len(self._orivals)

        self._tfvals = np.sort(self.stim_table['TF'].dropna().unique())
        self._number_tf = len(self._tfvals)

    def _get_osi(self, pref_tf, nc):
        """computes orientation and direction selectivity (cv) for cell

        :param pref_tf:
        :param nc:
        :return:
        """
        orivals_rad = np.deg2rad(self.orivals)
        tuning = self.response_events[:, pref_tf + 1, nc, 0]
        tuning = np.where(tuning > 0, tuning, 0)
        cv_top_os = np.empty(self.number_ori, dtype=np.complex128)
        cv_top_ds = np.empty(self.number_ori, dtype=np.complex128)
        for i in range(self.number_ori):
            cv_top_os[i] = (tuning[i] * np.exp(1j * 2 * orivals_rad[i]))
            cv_top_ds[i] = (tuning[i] * np.exp(1j * orivals_rad[i]))
        osi = np.abs(cv_top_os.sum()) / tuning.sum()
        dsi = np.abs(cv_top_ds.sum()) / tuning.sum()
        return osi, dsi

    def _get_reliability(self, specimen_id, st_mask):
        """Computes trial-to-trial reliability of cell at its preferred condition

        :param pref_ori:
        :param pref_tf:
        :param v:
        :return:
        """
        subset = self.sweep_events[st_mask][specimen_id].values
        subset += 1.0
        corr_matrix = np.empty((len(subset), len(subset)))
        for i in range(len(subset)):
            fri = get_fr(subset[i])
            for j in range(len(subset)):
                frj = get_fr(subset[j])
                # TODO: Is there a reason this method get fr[30:] and the another stim analysis classes gets fr[30:40]?
                #    We could consolidate this method across all the classes.
                r, p = st.pearsonr(fri[30:], frj[30:])
                corr_matrix[i, j] = r

        inds = np.triu_indices(len(subset), k=1)
        upper = corr_matrix[inds[0], inds[1]]
        return np.nanmean(upper)


    def _get_tfdi(self, pref_ori, nc):
        """Computes temporal frequency discrimination index for cell

        :param pref_ori:
        :param nc:
        :return: tf discrimination index
        """
        v = list(self.spikes.keys())[nc]
        tf_tuning = self.response_events[pref_ori, 1:, nc, 0]
        trials = self.mean_sweep_events[(self.stim_table['Ori'] == self.orivals[pref_ori])][v].values
        sse_part = np.sqrt(np.sum((trials-trials.mean())**2)/(len(trials)-5))
        return (np.ptp(tf_tuning))/(np.ptp(tf_tuning) + 2*sse_part)

    def _get_running_modulation(self, pref_ori, pref_tf, v):
        """Computes running modulation of cell at its preferred condition provided there are at least 2 trials for both
        stationary and running conditions

        :param pref_ori:
        :param pref_tf:
        :param v:
        :return: p_value of running modulation, mean response to preferred condition when running, mean response to
        preferred condition when stationary
        """
        subset = self.mean_sweep_events[(self.stim_table['TF'] == self.tfvals[pref_tf]) &
                                        (self.stim_table['Ori'] == self.orivals[pref_ori])]
        speed_subset = self.running_speed[(self.stim_table['TF'] == self.tfvals[pref_tf]) &
                                          (self.stim_table['Ori'] == self.orivals[pref_ori])]
        subset_run = subset[speed_subset.running_speed >= 1]
        subset_stat = subset[speed_subset.running_speed < 1]
        if np.logical_and(len(subset_run) > 1, len(subset_stat) > 1):
            run = subset[speed_subset.running_speed >= 1][v].mean()
            stat = subset[speed_subset.running_speed < 1][v].mean()
            if run > stat:
                run_mod = (run - stat)/run
            else:  # if stat > run:
                run_mod = -1 * (stat - run)/stat
            (_, p) = st.ttest_ind(subset_run[v], subset_stat[v], equal_var=False)
            return p, run_mod, run, stat
        else:
            return np.NaN, np.NaN, np.NaN, np.NaN

    def _get_suppressed_contrast(self, pref_ori, pref_tf, nc):
        """Computes two metrics to be used to identify cells that are suppressed by contrast

        :param pref_ori:
        :param pref_tf:
        :param nc:
        :return:
        """
        blank = self.response_events[0, 0, nc, 0]
        peak = self.response_events[pref_ori, pref_tf+1, nc, 0]
        all_resp = self.response_events[:, 1:, nc, 0].mean()
        peak_blank = peak - blank
        all_blank = all_resp - blank
        return peak_blank, all_blank

    def _fit_tf_tuning(self, pref_ori, pref_tf, nc):
        """Performs gaussian or exponential fit on the temporal frequency tuning curve at preferred orientation.

        :param pref_ori:
        :param pref_tf:
        :param nc:
        :return: index for the preferred tf from the curve fit, prefered tf from the curve fit, low cutoff tf from the
        curve fit, high cutoff tf from the curve fit
        """
        tf_tuning = self.response_events[pref_ori, 1:, nc, 0]
        fit_tf_ind = np.NaN
        fit_tf = np.NaN
        tf_low_cutoff = np.NaN
        tf_high_cutoff = np.NaN
        if pref_tf in range(1, 4):
            try:
                popt, pcov = curve_fit(gauss_function, range(5), tf_tuning, p0=[np.amax(tf_tuning), pref_tf, 1.],
                                       maxfev=2000)
                tf_prediction = gauss_function(np.arange(0., 4.1, 0.1), *popt)
                fit_tf_ind = popt[1]
                fit_tf = np.power(2, popt[1])
                low_cut_ind = np.abs(tf_prediction - (tf_prediction.max() / 2.))[:tf_prediction.argmax()].argmin()
                high_cut_ind = np.abs(tf_prediction - (tf_prediction.max() / 2.))[
                               tf_prediction.argmax():].argmin() + tf_prediction.argmax()
                if low_cut_ind > 0:
                    low_cutoff = np.arange(0, 4.1, 0.1)[low_cut_ind]
                    tf_low_cutoff = np.power(2, low_cutoff)
                elif high_cut_ind < 49:
                    high_cutoff = np.arange(0, 4.1, 0.1)[high_cut_ind]
                    tf_high_cutoff = np.power(2, high_cutoff)
            except Exception:
                pass
        else:
            fit_tf_ind = pref_tf
            fit_tf = self.tfvals[pref_tf]
            try:
                popt, pcov = curve_fit(exp_function, range(5), tf_tuning,
                                       p0=[np.amax(tf_tuning), 2., np.amin(tf_tuning)], maxfev=2000)
                tf_prediction = exp_function(np.arange(0., 4.1, 0.1), *popt)
                if pref_tf == 0:
                    high_cut_ind = np.abs(tf_prediction - (tf_prediction.max() / 2.))[
                                   tf_prediction.argmax():].argmin() + tf_prediction.argmax()
                    high_cutoff = np.arange(0, 4.1, 0.1)[high_cut_ind]
                    tf_high_cutoff = np.power(2, high_cutoff)
                else:
                    low_cut_ind = np.abs(tf_prediction - (tf_prediction.max() / 2.))[
                                  :tf_prediction.argmax()].argmin()
                    low_cutoff = np.arange(0, 4.1, 0.1)[low_cut_ind]
                    tf_low_cutoff = np.power(2, low_cutoff)
            except Exception:
                pass
        return fit_tf_ind, fit_tf, tf_low_cutoff, tf_high_cutoff


def do_sweep_mean(x):
    return len(x[x > 0.])/2.0


def gauss_function(x, a, x0, sigma):
    return a*np.exp(-(x-x0)**2/(2*sigma**2))


def exp_function(x, a, b, c):
    return a*np.exp(-b*x)+c