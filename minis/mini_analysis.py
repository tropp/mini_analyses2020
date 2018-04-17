from __future__ import print_function

"""
Analysis of miniature synaptic potentials
The analysis is driven by an imported dictionary.
Provides measure of event amplitude distribution and event frequency distribution

Requires a bunch of Manis' support routines, including ephysanalysis module to
read acq4 files; pylibrary utilities, digital filtering, clcments)bekkers for analysis 
of minis (we use the Andrade_Jonas algorithm for deconvolution in this pass).

Output summary is read by mini_summary_plots.py

Paul B. Manis, Ph.D. Jan-March 2018.

"""

import sys
import os
import argparse
from collections import OrderedDict
import numpy as np
import pickle
from matplotlib import rc
import matplotlib.pyplot as mpl
import matplotlib.gridspec as gridspec
import scipy.signal
import scipy.stats

import minis_methods as minis
import pylibrary.Utility as PU
import digital_filters as DF
import pylibrary.PlotHelpers as PH
import ephysanalysis as EP
from matplotlib.backends.backend_pdf import PdfPages

rc('text', usetex=False)
rc('font',**{'family':'sans-serif','sans-serif':['Verdana']})

# Example for the data table:
# self.basedatadir = '/Volumes/Pegasus/ManisLab_Data3/Sullivan_Chelsea/miniIPSCs'
# 
# datasets = {'m2a': {'dir': '2017.11.01_000/slice_000/cell_000',
#         'prots': [0], 'exclist': {0: [0,1 ]},
#         'thr': 2.5, 'rt': 0.35, 'decay': 4., 'G': 'WT'},
#     'm2c': {'dir': '2017.11.01_000/slice_000/cell_006',
#         'prots': [0, 1, 2, 3], 'exclist': {2: [7], 3:[1]},
#         'thr': 2.5, 'rt': 0.35, 'decay': 4., 'G': 'WT'},
#     'm4a': {'dir': '2017.11.06_000/slice_000/cell_000',
#         'prots': [0, 1], 'exclist': {0: [0,1], 1: [1, 6,7,8,9]},
#         'thr': 2.5, 'rt': 0.35, 'decay': 4., 'G': 'WT'},
#     }
#
# Where:
# each dict key indicates a cell from a mouse (mice are numbered, cells are lettered)
# 'dir': The main cell directory, relative to the base directory,
# 'prots': a list of the protocols to be analyzed,
# 'exclist': a dict of the protocols that have traces to be excluded
#     The excluded traces are in a tuple or list for each protocol.
# 'thr' : SD threshold for event detection (algorithm dependent)
# 'rt' : rise time for template (in msec)
# 'decay': decay time constant for the template (in msec)
# 'G' : group identifier (e.g, genotype, treatment, etc.)
# 
# 

class DataPlan():
    def __init__(self, datadictname):
        data = {}
        fn, ext = os.path.splitext(datadictname)
        if ext == '':
            ext = '.py'
        execfile(fn + ext, data)
        self.datasource = datadictname
        self.datasets = data['datasets']
        self.datadir = data['basepath']




def splitall(path):
    """
    split a filename into a list of individual directories, in order
    
    Parameters
    ----------
    path : str (no default)
        The path to be split
    
    Returns
    -------
    list : all elements of the path in order
    """
    
    allparts = []
    while 1:
        parts = os.path.split(path)
        if parts[0] == path:  # sentinel for absolute paths
            allparts.insert(0, parts[0])
            break
        elif parts[1] == path: # sentinel for relative paths
            allparts.insert(0, parts[1])
            break
        else:
            path = parts[0]
            allparts.insert(0, parts[1])
    return allparts


class MiniAnalysis():
    def __init__(self, dataplan):

        self.datasource = dataplan.datasource
        self.basedatadir = dataplan.datadir
        self.datasets = dataplan.datasets
    
    def analyze_all(self, fofilename, check=False):
        """
        Wraps analysis of individual data sets, writes plots to 
        a file named "summarydata%s.p % self.datasource" in pickled format.
    
        Parameters
        ----------
        fofilename : str (no default)
            name of the PDF plot output file
        check : bool (default: False)
            If true, run just checks for the existence of the data files, 
            but does no analysis.
    
        Returns
        -------
        Nothing
        """
        acqr = EP.acq4read.Acq4Read(dataname='Clamp1.ma')  # creates a new instance every time - probably should just create one.
        with PdfPages(fofilename) as pdf:
            summarydata = {}
            for id, mouse in enumerate(sorted(self.datasets.keys())):
                results = self.analyze_one(mouse, pdf, maxprot=10, arreader=acqr, check=check)
                summarydata[mouse] = results
        fout = 'summarydata_%s.p' % self.datasource
        fh = open(fout, 'wb')
        pickle.dump(summarydata, fh)
        fh.close()


    def analyze_one(self, mouse, pdf, maxprot=10, arreader=None, check=False):
        """
        Provide analysis of one entry in the data table using the Andrade_Joans algorithm
        and fitting distributions. 
        Generates a page with plots for each protocol/trace stacked, and histograms with
        fits for the interval and amplitude distributions
    
        Parameters
        ----------
        mouse : str (no default)
            key into the dictionary for the data to be analyzed
        pdf : pdf file object (no default):
            the pdf file object to write the plots to.
        maxprot : int (default: 10)
            Maximum numober of protocols to do in the analysis
        check : bool (default: False)
            If true, run just checks for the existence of the data files, 
            but does no analysis.    

    
        Returns
        -------
            cell summary dictionary for the 'mouse' entry. 
        """
        if arreader is None:
            acqr = EP.acq4read.Acq4Read(dataname='Clamp1.ma')  # only if we don't already have one
        else:
            acqr = arreader
        print('arreader: ', arreader)
        print('acqr: ', acqr)
        rasterize = True    # set True to rasterize traces to reduce size of final document
                            # set false for pub-quality output (but large size)
        dt = 0.1
        mousedata = self.datasets[mouse]
        print ('\nMouse: ', mouse)
        genotype = mousedata['G']
        excl = mousedata['exclist']  # get the exclusion list
        cell_summary={'intervals': [], 'amplitudes': [], 'protocols': [], 'eventcounts': [], 'genotype': genotype, 'mouse': mouse, 
            'amplitude_midpoint': 0.}
    
        if not check:
            sizer = OrderedDict([('A', {'pos': [0.12, 0.8, 0.35, 0.60]}),
                           #  ('A1', {'pos': [0.52, 0.35, 0.35, 0.60]}),
                             ('B', {'pos': [0.12, 0.35, 0.08, 0.20]}),
                             ('C', {'pos': [0.60, 0.35, 0.08, 0.20]}),
                             ])  # dict elements are [left, width, bottom, height] for the axes in the plot.
            n_panels = len(sizer.keys())
            gr = [(a, a+1, 0, 1) for a in range(0, n_panels)]   # just generate subplots - shape does not matter
            axmap = OrderedDict(zip(sizer.keys(), gr))
            P = PH.Plotter((n_panels, 1), axmap=axmap, label=True, figsize=(7., 9.))
            P.resize(sizer)  # perform positioning magic
            hht = 3
            ax0 = P.axdict['A']
            ax0.set_ylabel('pA', fontsize=9)
            ax0.set_xlabel('T (ms)', fontsize=9)
            #axdec = P.axdict['A1']
            axIntvls = P.axdict['B']
            axIntvls.set_ylabel('Fraction of Events', fontsize=9)
            axIntvls.set_xlabel('Interevent Interval (ms)', fontsize=9)
            axIntvls.set_title('mEPSC Interval Distributon', fontsize=10)
            axAmps = P.axdict['C']
            axAmps.set_ylabel('Fraction of Events', fontsize=9)
            axAmps.set_xlabel('Event Amplitude (pA)', fontsize=9)
            axAmps.set_title('mEPSC Amplitude Distribution', fontsize=10)
    
        datanameposted = False
        yspan = 40.
        ypqspan = 2000.
        ntr = 0
        # for all of the protocols that are included for this cell (identified by mouse and a letter)
        print ('mousedata prots: ', mousedata['prots'])

        for nprot, dprot in enumerate(mousedata['prots']):
            if nprot > maxprot:
                return
            exclude_traces = []
            if len(mousedata['exclist']) > 0:  # any traces to exclude?
                if dprot in mousedata['exclist'].keys():
                    #print (mousedata['exclist'], dprot, nprot)
                    exclude_traces = mousedata['exclist'][dprot]
                
            try:
                fn = os.path.join(self.basedatadir, mousedata['dir'], ('minis_{0:03d}'.format(dprot)))
            except:
                print('path failed to join')
                print ('    ', dprot, nprot)
                print ('    ', mousedata['prots'])
                print('     ', dprot, mousedata['prots'][nprot])
                exit(1)
            if 'sign' not in mousedata.keys():
                sign = -1
            else:
                sign = mousedata['sign']
            if not check:
                print('Protocol file: ', fn)
                print('   sign: ', sign)
            split = splitall(fn)[-4:-1]
            dataname = ''
            for i in range(len(split)):
                dataname = os.path.join(dataname, split[i])
            if not check:
                print('  Protocol dataname: ', dataname)
                print('  exclude traces: ',  exclude_traces)
            if not datanameposted and not check:
                P.figure_handle.suptitle(mouse + '  ' + dataname + ' : ' + genotype, fontsize=9, weight='bold')
                datanameposted = True
            # print('a')
            dainfo = fn
            # print('a1')
            acqr.setProtocol(fn)
            print('set protocol', check)
            if check:
                try:
                    result = acqr.getData(check=True)
                    if result is False:
                        sys.stdout.write(r'\n\033[0;31m******* Get data failed to find a file ', fn, dataname, ' *******\033[0;97m\n')
                    else:
                        # print('getdata finished')
                        continue
                except:
                    print('  ******* Get data failed... in acqr.getData ', fn, dataname)
                    continue
            
            # print('c')
            if check:
                # print('continuing')
                continue
            acqr.getData()
            # except:
            #     print('  Get data failed... ')
            #     continue
            print('got data')
            data = np.array(acqr.data_array)
            time_base = acqr.time_base
            dt = 1000.*acqr.sample_interval  # in sec... so convert to msec
            data = data*1e12  # convert to pA
            aj = minis.AndradeJonas()
            time_base = time_base*1000.
            maxt = np.max(time_base)
    #        print('mousedata rt: ', mousedata['rt'], '   mousedata decay: ', mousedata['decay'])
            aj.setup(tau1=mousedata['rt'], tau2=mousedata['decay'], template_tmax=maxt, dt=dt, delay=0.0, sign=-1)
            mwin = int(2*(4.)/dt)
            order = int(1.0/dt)
        
            ntraces = data.shape[0]
            tracelist = range(ntraces)
            print ('  tracelist: ', tracelist, ' exclude: ', exclude_traces)
            ax0.set_xlim(-1500., np.max(acqr.time_base)*1000.)
            nused = 0
            # for all of the traces collected in this protocol run
            for i in tracelist:
                yp = (ntr+i)*yspan
                ypq = (ntr*i)*ypqspan
                linefit= np.polyfit(time_base, data[i], 1)
                refline = np.polyval(linefit, time_base)
                data[i] = data[i] - refline
                odata = data[i].copy()
                ax0.text(-1200, yp, '%03d %d' % (dprot, i), fontsize=8)  # label the trace
                if i in exclude_traces:  # plot the excluded traces, but do not analyze them
                    ax0.plot(acqr.time_base*1000., odata + yp ,'y-', linewidth=0.25, alpha=0.25, rasterized=rasterize)
                    continue
                nused = nused + 1
            
                frs, freqs = PU.pSpectrum(data[i], samplefreq=1000./dt)
                # NotchFilter(signal, notchf=[60.], Q=90., QScale=True, samplefreq=None):
                data[i] = DF.NotchFilter(data[i], [60., 120., 180., 240., 300., 360, 420., 
                                480., 660., 780., 1020., 1140., 1380., 1500., 4000.], Q=20., samplefreq=1000./dt)
                dfilt = DF.SignalFilter_HPFButter(np.pad(data[i], (len(data[i]), len(data[i])), mode='median', ), 2.5, 1000./dt, NPole=4)
                dfilt = DF.SignalFilter_LPFButter(dfilt, 2800., 1000./dt, NPole=4)
    #            frsfilt, freqsf = PU.pSpectrum(dfilt, samplefreq=1000./dt)
                data[i] = dfilt[len(data[i]):2*len(data[i])]

                aj.deconvolve(data[i], thresh=float(mousedata['thr']), llambda=10., order=order)

                # summarize data
                aj.summarize(data[i])
                #aj.plots()
                intervals = np.diff(aj.timebase[aj.onsets])
                cell_summary['intervals'].extend(intervals)
                # peaks = []
                # amps = []
                # for j in range(len(aj.onsets)):
                #      p =  scipy.signal.argrelextrema(aj.sign*data[i][aj.onsets[j]:(aj.onsets[j]+mwin)], np.greater, order=order)[0]
                #      if len(p) > 0:
                #          peaks.extend([int(p[0]+aj.onsets[j])])
                #          amp = aj.sign*data[i][peaks[-1]] - aj.sign*data[i][aj.onsets[j]]
                #          amps.extend([amp])
                cell_summary['amplitudes'].extend(sign*data[i][aj.smpkindex])
                cell_summary['eventcounts'].append(len(intervals))
                cell_summary['protocols'].append((nprot, i))
                ax0.plot(aj.timebase, odata + yp ,'c-', linewidth=0.25, alpha=0.25, rasterized=rasterize)
                ax0.plot(aj.timebase, data[i] + yp, 'k-', linewidth=0.25, rasterized=rasterize)
                ax0.plot(aj.timebase[aj.smpkindex], data[i][aj.smpkindex] + yp, 'ro', markersize=1.75, rasterized=rasterize)

                if 'A1' in P.axdict.keys():
                    axdec.plot(aj.timebase[:aj.quot.shape[0]],  aj.quot, label='Deconvolution') 
                    axdec.plot([aj.timebase[0],aj.timebase[-1]],  [aj.sdthr,  aj.sdthr],  'r--',  linewidth=0.75, 
                            label='Threshold ({0:4.2f}) SD'.format(aj.sdthr))
                    axdec.plot(aj.timebase[aj.onsets]-aj.idelay,  ypq + aj.quot[aj.onsets],  'y^', label='Deconv. Peaks')
        #            axdec.plot(aj.timebase, aj.quot+ypq, 'k', linewidth=0.5, rasterized=rasterize)
            ntr = ntr + len(tracelist)# - len(exclude_traces)
            if nused == 0:
                continue
        if check:
            return  # no processing
        # summarize the event and amplitude distributions
        # For the amplitude, the data are fit against normal, skewed normal and gamma distributions.

        # generate histogram of amplitudes for plots
        histBins = 50
        nsamp = 1
        nevents = len(cell_summary['amplitudes'])
        if nevents < 100:
            return cell_summary
        amp, ampbins, amppa= axAmps.hist(cell_summary['amplitudes'], histBins, alpha=0.5, density=True)

        # fit to normal distribution
        ampnorm = scipy.stats.norm.fit(cell_summary['amplitudes'])  #
        print('    Amplitude (N={0:d} events) Normfit: mean {1:.3f}   sigma: {2:.3f}'.format(nevents, ampnorm[0], ampnorm[1]))
        x = np.linspace(scipy.stats.norm.ppf(0.01, loc=ampnorm[0], scale=ampnorm[1]),
                         scipy.stats.norm.ppf(0.99, loc=ampnorm[0], scale=ampnorm[1]), 100)
        axAmps.plot(x, scipy.stats.norm.pdf(x, loc=ampnorm[0], scale=ampnorm[1]),
                'r-', lw=2, alpha=0.6, label='Norm: u={0:.3f} s={1:.3f}'
                    .format(ampnorm[0], ampnorm[1]))
        k2, p = scipy.stats.normaltest(cell_summary['amplitudes'])
        print("    p (amplitude is Gaussian) = {:g}".format(1-p))
        print('    Z-score for skew and kurtosis = {:g} '.format(k2))
    
        # fit to skewed normal distriubution
        ampskew = scipy.stats.skewnorm.fit(cell_summary['amplitudes'])
        print('    ampskew: mean: {0:.4f} skew:{1:4f}  scale/sigma: {2:4f} '.format(ampskew[1], ampskew[0], 2*ampskew[2]))
        x = np.linspace(scipy.stats.skewnorm.ppf(0.002, a=ampskew[0], loc=ampskew[1], scale=ampskew[2]),
                         scipy.stats.skewnorm.ppf(0.995, a=ampskew[0], loc=ampskew[1], scale=ampskew[2]), 100)
        axAmps.plot(x, scipy.stats.skewnorm.pdf(x, a=ampskew[0], loc=ampskew[1], scale=ampskew[2]),
                'm-', lw=2, alpha=0.6, label='skewnorm a={0:.3f} u={1:.3f} s={2:.3f}'
                    .format(ampskew[0], ampskew[1], ampskew[2]))

        # fit to gamma distriubution
        ampgamma = scipy.stats.gamma.fit(cell_summary['amplitudes'], loc=0.)
        gamma_midpoint = scipy.stats.gamma.ppf(0.5, a=ampgamma[0], loc=ampgamma[1], scale=ampgamma[2])  # midpoint of distribution
        print('    ampgamma: mean: {0:.4f} gamma:{1:.4f}  loc: {1:.4f}  scale: {3:.4f} midpoint: {4:.4f}'.
                        format(ampgamma[0]*ampgamma[2], ampgamma[0], ampgamma[2],  ampgamma[2], gamma_midpoint))
        x = np.linspace(scipy.stats.gamma.ppf(0.002, a=ampgamma[0], loc=ampgamma[1], scale=ampgamma[2]),
                         scipy.stats.gamma.ppf(0.995, a=ampgamma[0], loc=ampgamma[1], scale=ampgamma[2]), 100)
        axAmps.plot(x, scipy.stats.gamma.pdf(x, a=ampgamma[0], loc=ampgamma[1], scale=ampgamma[2]),
                'g-', lw=2, alpha=0.6, label='gamma: a={0:.3f} loc={1:.3f}\nscale={2:.3f}, mid={3:.3f}'
                    .format(ampgamma[0], ampgamma[1], ampgamma[2], gamma_midpoint) ) #ampgamma[0]*ampgamma[2]))
        axAmps.plot([gamma_midpoint, gamma_midpoint], [0., scipy.stats.gamma.pdf([gamma_midpoint],
                        a=ampgamma[0], loc=ampgamma[1], scale=ampgamma[2])], 'k--',
                        lw=2, alpha=0.5)
        axAmps.legend(fontsize=6)
        cell_summary['amplitude_midpoint'] = gamma_midpoint

        #
        # Interval distribution
        #
        an, bins, patches = axIntvls.hist(cell_summary['intervals'], histBins, density=True)
        nintvls = len(cell_summary['intervals'])
        expDis = scipy.stats.expon.rvs(scale=np.std(cell_summary['intervals']), loc=0, size=nintvls)
        # axIntvls.hist(expDis, bins=bins, histtype='step', color='r')
        ampexp = scipy.stats.expon.fit(cell_summary['intervals'])
        x = np.linspace(scipy.stats.expon.ppf(0.01, loc=ampexp[0], scale=ampexp[1]),
                         scipy.stats.expon.ppf(0.99, loc=ampexp[0], scale=ampexp[1]), 100)
        axIntvls.plot(x, scipy.stats.expon.pdf(x, loc=ampexp[0], scale=ampexp[1]),
                'r-', lw=3, alpha=0.6, label='Exp: u={0:.3f} s={1:.3f}\nMean Interval: {2:.3f}\n#Events: {3:d}'
                    .format(ampexp[0], ampexp[1], np.mean(cell_summary['intervals']), len(cell_summary['intervals'])))
        axIntvls.legend(fontsize=6)

        # report results
        print('   N events: {0:7d}'.format(nintvls))
        print('   Intervals: {0:7.1f} ms SD = {1:.1f} Frequency: {2:7.1f} Hz'.format(np.mean(cell_summary['intervals']),
                np.std(cell_summary['intervals']), 1e3/np.mean(cell_summary['intervals'])))
        print('    Amplitude: {0:7.1f} pA SD = {1:.1f}'.format(np.mean(cell_summary['amplitudes']), np.std(cell_summary['amplitudes'])))
    
        # test if interval distribtuion is poisson:
        stats = scipy.stats.kstest(expDis,'expon', args=((np.std(cell_summary['intervals'])),), alternative = 'two-sided')
        print('    KS test for intervals Exponential: statistic: {0:.5f}  p={1:g}'.format(stats.statistic, stats.pvalue))
        stats_amp = scipy.stats.kstest(expDis,'norm', 
                args=(np.mean(cell_summary['amplitudes']),
                      np.std(cell_summary['amplitudes'])), alternative = 'two-sided')
        print('    KS test for Normal amplitude: statistic: {0:.5f}  p={1:g}'.format(stats_amp.statistic, stats_amp.pvalue))

        # show to sceen or save plots to a file
        if pdf is None:
            mpl.show()
        else:
            pdf.savefig(dpi=300)  # rasterized to 300 dpi is ok for documentation.
            mpl.close()
        return cell_summary

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='mini synaptic event analysis')
    parser.add_argument('datadict', type=str,
                        help='data dictionary')
    parser.add_argument('-o', '--one', type=str, default='', dest='do_one',
                        help='just do one')
    parser.add_argument('-c', '--check', action='store_true',
                        help='Check for files; no analysis')

    args = parser.parse_args()

    dataplan = DataPlan(args.datadict)
    
    MI = MiniAnalysis(dataplan)
    
    if args.do_one == '': # no second argument, run all data sets
        MI.analyze_all(fofilename='all_%s.pdf' % args.datadict, check=args.check)
    else:
        MI.analyze_one(args.do_one, pdf=None, maxprot=10, check=args.check)