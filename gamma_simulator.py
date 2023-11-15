import numpy as np
import scipy.stats as stats
import matplotlib.pyplot as plt
import seaborn as sns
import urllib


# %% Classes
class gamma_simulator:
    def __init__(self,
                 energy_histogram: str or dict = 'Co-60',
                 signal_len: int or float = 1024,
                 lambda_value: float = 0.1,
                 fs: float = 1,
                 dict_size: int = 100,
                 dict_type: str = 'double_exponential',
                 dict_params=None,
                 noise: float = 0.01,
                 noise_unit='std',
                 seed: int = None,
                 enforce_edges: bool = True,
                 verbose: bool = False,
                 verbose_plots: bool = False):
        """Simulate a gamma signal with the following parameters:
        energy_histogram: name of the energy_histogram from the
            gamma spectrum database (https://gammadb.nuclearphoenix.xyz/)
            or a dictionary with energy and counts values
            {'hist_energy':hist_energy, 'hist_counts':hist_counts}
        signal_len: length of the signal in seconds
        lambda_value: event rate in Hz (events per second)
        fs: sampling frequency in Hz
        dict_size: size of the dictionary
        dict_type: type of the dictionary (currently only double_exponential is supported)
        dict_params: dictionary parameters (dictionary of parameters)
            for double_exponential:
                tau1_mean: mean value of the first time constant
                tau1_std: standard deviation of the first time constant
                tau2_mean: mean value of the second time constant
                tau2_std: standard deviation of the second time constant
        noise: noise level according to noise_unit
        noise_unit: 'std' for standard deviation of per sample noise,
                    'snr' for signal-to-noise ratio in dB
        enforce_edges: if True, any pulse shape have its starting or ending points within the signal
        seed: random seed for reproducibility
        verbose: print general information
        verbose_plots: illustrative plots
        """
        self.verbose = verbose
        self.verbose_plots = verbose_plots
        self.seed = seed
        self.enforce_edges = enforce_edges
        # --- load the spectrum ---
        # hist_energy: energy values [keV] (bins, x-axis
        # hist_counts: amount of counts for each hist_energy value (y-axis)
        if isinstance(energy_histogram,str):
            # load the spectrum from the database with isotope name = energy_histogram
            self.energy_desc = energy_histogram
            self.hist_energy, self.hist_counts = self.load_spectrum_data()
        elif isinstance(energy_histogram, dict):
            # load the spectrum from the dictionary
            self.energy_desc = None
            self.hist_energy = energy_histogram['hist_energy']
            self.hist_counts = self.normalize_spectrum_histogram(energy_histogram['hist_counts'])
            assert self.hist_energy.shape == self.hist_counts.shape, "Counts and energies must have the same shape"
        else:
            raise ValueError(f'Unknown energy_histogram type: {type(energy_histogram)}')
        if self.verbose_plots:
            self.verbose_plot_energy()

        self.fs = fs
        self.dt = 1 / self.fs  # sampling period
        # --- shape dictionary generation ---
        self.dict_size = dict_size
        self.dict_type = dict_type
        if dict_params is None:
            dict_params = {'tau1_mean': 0.01,
                           'tau1_std': 0.001,
                           'tau2_mean': 0.1,
                           'tau2_std': 0.001}
        self.dict_params = dict_params
        # shape_len_sec: length of the shape in seconds
        # shape_len: length of the shape in samples
        # t_rise: rise time in seconds or samples
        self.shape_len, self.shape_len_sec, self.t_rise = self.evaluate_shape_len()
        self.t_fall = self.shape_len_sec - self.t_rise  # fall time
        # --- Time parameters ---
        # lambda_value: event rate in event/second
        self.lambda_value = lambda_value
        # lambda_n: event rate in event/sample
        self.lambda_n = lambda_value * self.dt  # = lambda_value / fs
        # signal_len_sec: length of the signal in seconds
        self.signal_len_sec = signal_len
        # signal_len: length of the signal in samples
        self.signal_len = int(self.signal_len_sec * self.fs)
        if self.signal_len < self.shape_len:
            raise ValueError(f'Signal length must be greater than the shape length')
        if not self.enforce_edges:
            # The shape is DO allowed to exceed the signal boundaries
            # The signal is extended by 2 * shape_len_sec
            self.signal_len += self.shape_len * 2

        self.noise = noise
        assert noise_unit in ['std', 'snr'], "Unknown noise unit"
        self.noise_unit = noise_unit
        # calculate the duty cycle
        self.duty_cycle = self.lambda_value * self.shape_len_sec
        # parameters of the signal (will be calculated later)
        self.lambda_measured = None
        self.energies = None
        self.n_events = None
        self.times = None
        self.measured_snr = None
        self.tau1 = None
        self.tau2 = None

    def verbose_info(self):
        """Print general information about the simulated signal.
        """
        print('-- General information ------------------------------------------')
        if self.energy_desc is None:
            print(f'Loaded spectrum from the dictionary')
        else:
            print(f'Loaded spectrum for {self.energy_desc} isotope')

        print(f'Energy spectrum between {self.hist_energy.min()} and {self.hist_energy.max()} keV '
              f'with {self.hist_energy.shape[0]} bins')
        if self.enforce_edges:
            print(f'Shapes are NOT allowed to exceed the signal boundaries')

        if self.fs == 1:  # discrete time
            print(f'Discrete-time parameters are used!')
            print(f'Signal length : {self.signal_len_sec} samples')
            print(f'Number of events: {self.n_events} (randomly generated)')
            print(f'Activity {self.lambda_value:.3f} event per sample and '
                  f'actual activity is {self.lambda_measured:.3f} events per sample')
            print(f'Shape model: {self.dict_type}')
            print(f'Number of {self.dict_type} shapes in the dictionary: {self.dict_size}')
            print(f'Shape parameters: tau1 = {self.dict_params["tau1_mean"]}±{self.dict_params["tau1_std"]} '
                  f'and tau2 = {self.dict_params["tau2_mean"]}±{self.dict_params["tau2_std"]}')
            print(f'Each shape has a maximum length of {self.shape_len_sec * self.fs:.3f} samples '
                  f'rounded to {self.shape_len} samples')
            print(f'Rise time is {self.t_rise:.3f} samples and fall time is {self.t_fall:.3f} samples')
        else:  # continuous time
            print(f'Sampling frequency: {self.fs} samples per second')
            print(f'Signal length is {self.signal_len_sec} sec that '
                  f'are {int(self.signal_len_sec * self.fs)} samples')
            print(f'Events: {self.n_events} (randomly generated)')
            print(f'Activity {self.lambda_value:.3f} event per second and '
                  f'actual activity is {self.lambda_measured:.3f} events per second')
            print(f'Normalized lambda value: {self.lambda_n:.3e} events per sample')
            print(f'Shape model: {self.dict_type}')
            print(f'Number of {self.dict_type} shapes in the dictionary: {self.dict_size}')
            print(f'Shape parameters: tau1 = {self.dict_params["tau1_mean"]} sec ±{self.dict_params["tau1_std"]:1.3e}'
                  f' ({self.dict_params["tau1_mean"] * self.fs:.2f} samples) '
                  f'and tau2 = {self.dict_params["tau2_mean"]} sec ±{self.dict_params["tau2_std"]:0.3e}'
                  f' ({self.dict_params["tau2_mean"] * self.fs:.2f} samples) ')
            print(f'Each shape has a maximum length of {self.shape_len_sec:1.3e} sec that are {self.shape_len} samples')
            print(f'Rise time is {self.t_rise:.3e} sec and fall time is {self.t_fall:.3e} sec')
        # duty cycle and pile-up probability
        print(f'Duty cycle is given by {self.duty_cycle:.2f}'
              f' with theoretical pile-up probability of {1 - np.exp(-self.duty_cycle):.3f}')
        print(f'Actual pile-up probability is {self.pile_up_stat() / self.n_events:.3f} with'
              f' {self.n_events - self.pile_up_stat()} non-pile-up events'
              f' out of {self.n_events} events')
        # noise
        if self.noise_unit == 'std':
            print(f'Noise level: ±{self.noise} per sample')
        elif self.noise_unit == 'snr':
            print(f'Noise level: {self.noise} dB')
        print(f'Measured SNR: {self.measured_snr:.2f} dB')
        # random seed
        if self.seed is None:
            print('Random seed is not defined')
        else:
            print(f'Pre-defined random seed is used: {self.seed}')
        # return ''
        self.pile_up_stat()

    def pile_up_stat(self):
        """evaluate number of the pile-ups in the generated signal"""
        # calculate the time difference between events
        d_time = self.times[1:] - self.times[:-1]
        # pile-up happens when the time difference less than the shape length
        pile_ups = np.sum(d_time < self.shape_len)
        return pile_ups

    def verbose_plot_energy(self):
        """Plot the energy histogram"""
        plt.bar(self.hist_energy, self.hist_counts, width=1, alpha=0.5)
        plt.yscale('log')
        plt.grid(linestyle='--', linewidth=1, color='gray')
        plt.xlabel('Energy [keV]')
        plt.ylabel('Normalized counts')
        if self.energy_desc is None:
            plt.title(f'User-defined energy spectrum with {self.hist_energy.shape[0]} bins')
        else:
            plt.title(f'{self.energy_desc} energy spectrum with {self.hist_energy.shape[0]} bins')
        plt.show()

    # --- Energy spectrum -----------------------------------------------------
    @staticmethod
    def normalize_spectrum_histogram(counts: np.ndarray) -> np.ndarray:
        """Normalize the spectrum to the number of counts
        The resulting histogram is PDF-normalized to 1
        hist_energy: hist_energy values
        counts: counts values
        return: tuple of normalized hist_energy and counts
        """
        counts = counts / counts.sum()
        return counts

    def load_spectrum_data(self) -> tuple[np.ndarray, np.ndarray]:
        """Load the gamma spectrum database from https://github.com/OpenGammaProject/Gamma-Spectrum-Database/
        energy_histogram: name of the energy_histogram
        return: tuple of hist_energy and counts, where counts are normalized to 1
        """
        url = ('https://raw.githubusercontent.com/bykhov/Gamma-Spectrum-Database/main/assets/spectra/'
               + self.energy_desc
               + '.html')
        try:
            html_page = urllib.request.urlopen(url).read()
        except urllib.error.HTTPError:
            raise ValueError(f'Unknown isotope name: {self.energy_desc}')

        # parse the html page and extract the spectrum
        page = str(html_page)
        start_idx = page.find('Clean Spectrum')
        x_idx = page.find('"x":', start_idx) + 5
        x_str = page[x_idx:page.find(']', x_idx)]
        x = np.fromstring(x_str, sep=',')
        y_idx = page.find('"y":', x_idx) + 5
        y_str = page[y_idx:page.find(']', y_idx)]
        y = np.fromstring(y_str, sep=',')
        y[y < 0] = 0  # remove weird negative values
        energy, counts = x, self.normalize_spectrum_histogram(y)
        return energy, counts

    # --- Time ---------------------------------------------------------------
    def generate_arrival_times(self,
                               outage_prob: float = 1e-12) -> np.ndarray:
        """Generate a sequence of events with a given number of samples and events rate.
        signal_len: length of the signal in samples
        lambda_n: event rate in event/sample
        outage_prob: probability of missing events in a frame of length signal_len (not used)
        return: array of arrival times
        """
        # vector implementation does not check for number of events through a loop (!)
        # cdf(outage_prob) is used to calculate the number of events to generate
        # the number of events is calculated according to the Poisson distribution

        np.random.seed(self.seed)
        # number of events in a frame to guarantee outage probability
        max_number_of_events = stats.poisson.ppf(1 - outage_prob, mu=self.lambda_n * self.signal_len).astype(int)
        # generate events times
        times = np.cumsum(np.random.exponential(1 / self.lambda_n, max_number_of_events))
        # remove events after the end of the frame
        times = times[times < self.signal_len - self.shape_len]
        return times

    def generate_energy_distribution(self) -> np.ndarray:
        """Generate a hist_energy distribution according to the number of events (per each arrival time).
        Each hist_energy value corresponds to the arrival time of an event.
        return: array of hist_energy values
        """

        # generate random hist_energy values
        np.random.seed(self.seed)
        energies = np.random.choice(self.hist_energy,
                                    size=self.n_events,
                                    replace=True,
                                    p=self.hist_counts)
        return energies

    # --- Shapes -------------------------------------------------------------
    def evaluate_shape_len(self) -> tuple[int, float, float]:
        """Evaluate the length of the shape
        return:
            shape_len: length of the shape in samples
            shape_time: length of the shape in seconds
            t_rise: rise time in seconds (always)
        """
        assert self.dict_params['tau2_mean'] > self.dict_params['tau1_mean'], "tau2 must be greater than tau1"
        if self.dict_type == 'double_exponential':
            shape_time = 6 * (self.dict_params['tau2_mean'] + 3 * self.dict_params['tau2_std'])
            shape_len = int(shape_time * self.fs)
            # calculate the rise time
            tr = ((self.dict_params["tau1_mean"] * self.dict_params["tau2_mean"]) /
                  (self.dict_params["tau1_mean"] + self.dict_params["tau2_mean"]) *
                  np.log(self.dict_params["tau2_mean"] / self.dict_params["tau1_mean"]))
        else:
            raise ValueError(f'Unknown shape type: {self.dict_type}')
        return shape_len, shape_time, tr

    def generate_double_exponent_shape_parameters(self) -> tuple[np.ndarray, np.ndarray]:
        """Generate random parameters for the double exponential shape.
        return: tuple of tau1 and tau2 are shape parameters for each event in the signal
        """
        np.random.seed(self.seed)
        # generate random parameters
        # tau1values and tau2values are parameters for each shape in the dictionary
        tau1values = np.random.normal(self.dict_params['tau1_mean'], self.dict_params['tau1_std'], self.dict_size)
        tau2values = np.random.normal(self.dict_params['tau2_mean'], self.dict_params['tau2_std'], self.dict_size)
        assert np.all(tau1values > 0), "tau1 must be positive - please check the parameters"
        assert np.all(tau2values > 0), "tau2 must be positive - please check the parameters"
        # assign the parameters to events
        # sample from the dictionary for each event
        # reshape to column vector for broadcasting
        tau1 = np.random.choice(tau1values, size=self.n_events, replace=True).reshape(-1, 1)
        tau2 = np.random.choice(tau2values, size=self.n_events, replace=True).reshape(-1, 1)
        return tau1, tau2

    def generate_double_exponent_shape_dict(self) -> np.ndarray:
        """Generate a double exponential shape dictionary.
        Shape is generated for each event in the signal.
        """
        # generate random parameters
        self.tau1, self.tau2 = self.generate_double_exponent_shape_parameters()
        # define x-axis values:
        # dimension 0 is the number of shapes, dimension 1 is the number of samples
        # each start time is shifted by the arrival time of the event
        n = np.r_[0:self.shape_len] - np.reshape(self.times / self.dt % 1.0, (-1, 1))
        s = np.exp(-n * self.dt / self.tau2) - np.exp(-n * self.dt / self.tau1)
        s[:, 0] = 0  # set the first sample to zero
        # normalize the shape
        s /= s.sum(axis=1, keepdims=True)
        if self.verbose_plots:
            # plot the shapes without random start times
            n_plot = np.r_[0:self.shape_len]
            s_plot = np.exp(-n_plot * self.dt / self.tau2) - np.exp(-n_plot * self.dt / self.tau1)
            plt.plot(n_plot, s_plot.T, alpha=0.25)
            plt.grid(linestyle='--', linewidth=1, color='gray')
            plt.xlabel('Time [n]')
            plt.ylabel('Amplitude')
            plt.title(f'Shapes in the pulse shapes dictionary')
            plt.xlim([0, self.shape_len])
            plt.axis('tight')
            plt.show()
        return s

    # --- Signal -------------------------------------------------------------
    # Signal is generated without enforcing the edges and then the edges are truncated if needed
    def generate_signal_without_noise(self) -> np.ndarray:
        """Generate a noiseless signal.
        """
        # generate the dictionary
        if self.dict_type == 'double_exponential':
            s = self.generate_double_exponent_shape_dict()
        else:
            raise ValueError(f'Unknown shape type: {self.dict_type}')
        # generate the signal
        signal = np.zeros(self.signal_len)
        for i in range(self.n_events):
            # for each event
            # add the shape to the signal
            signal[int(self.times[i]):int(self.times[i]) + self.shape_len] += self.energies[i] * s[i, :]

        return signal

    def generate_signal_with_noise(self) -> np.ndarray:
        """Add noise to the signal.
        """
        np.random.seed(self.seed)
        # generate the noiseless signal
        signal = self.generate_signal_without_noise()
        # generate white Gaussian noise
        if self.noise_unit == 'std':
            noise_std = self.noise
        else:  # if self.noise_unit == 'snr':
            noise_std = 10 ** (-self.noise / 20) * np.linalg.norm(signal) / np.sqrt(len(signal))
        noise = np.random.normal(0, scale=noise_std, size=self.signal_len)
        self.measured_snr = 20 * np.log10(np.linalg.norm(signal) / np.linalg.norm(noise))
        # add noise to the signal
        signal += noise
        return signal

    def generate_signal(self) -> np.ndarray:
        # times: event times
        # n_events: number of events in times vector
        self.times = self.generate_arrival_times()
        self.n_events = len(self.times)
        self.lambda_measured = self.n_events / self.signal_len_sec  # actual event rate
        # energy: energy values for each event
        self.energies = self.generate_energy_distribution()
        signal = self.generate_signal_with_noise()
        if not self.enforce_edges:
            # remove the edges
            signal = signal[self.shape_len:-self.shape_len]
        # verbose information
        if self.verbose_plots:
            if len(signal) > 2000:
                # plot only the first 2000 samples is signal is too long
                plt.plot(signal[:2000])
                plt.title(f'Signal with additive noise (first 2000 samples)')
            else:
                plt.plot(signal)
                plt.title(f'Signal with additive noise')
            plt.xlabel('Time [n]')
            plt.ylabel('Signal amplitude')
            plt.title(f'Signal with additive noise')
            plt.axis('tight')
            plt.grid()
            plt.show()
        if self.verbose:
            self.verbose_info()
        return signal

