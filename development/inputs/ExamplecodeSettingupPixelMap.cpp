//Updated list of included headers ---
#include <iostream>
#include <fstream>
#include <sstream>
#include <string>
#include <cstring>
#include "stdio.h"
#include "stdlib.h"
#include "time.h"
#include "math.h"
#include "TROOT.h"
#include "TFile.h"
#include "TTree.h"
#include "TGraph.h"
#include "TGraph2D.h"
#include "TH1.h"
#include "TH2.h"
#include "TF1.h"
#include "TStyle.h"
#include "TH2Poly.h"
#include "TCanvas.h"
#include "TRandom3.h"
#include "Math/Interpolator.h"
using namespace std;
//------------------------------------

struct HexPixel {

	string pxLabel;
	double pxID;
	double prID;
	double RingID;
	double PrBoard;
	double FETBoard;
	double radius;
	double angle;
	double center[2]; // [0] = x [mm] | [1] = y [mm]
	double xEdges[7];
	double yEdges[7];
	TGraph *gr;
	TGraph *grL;
	double nWF;
};

struct Detector {

	const int nHexPlots = 6;
	HexPixel pixlesPrmp[127];
	// HexPixel pixlesFET[127];
	TH2Poly *HexPlotPrmp;
};


struct fitHisto { // This object is specific to a simulation file, which can be more than 1 per input

	TH1D *hr;
	TH1F *hg;
	bool iniFlag;
	string fname;
	double hcounts;
	double hcounts2;

	// Sigma here in this struct is a driven variable.
	// It isn't meant to be changed by the user or the script
	// Its purpose is to return the value of the sigma that was used for hg
	double sigma;

	double intpMin;
	double intpMax;

	ROOT::Math::Interpolator* simModel;
};

struct fitResults { // This object can stay unchanged for a given input

	string fname;

	int BC;
	int pixel;
	int hchannel;
	int findex;
	TH1F *hrun;

	double chi2;
	double NDF;

	bool iniFlag;
	bool fitIni;
	double sigma; // This is the driving variable used as a knob to change the sigma
	//
	double pars[3];
	double parErrs[3];

	double EMax;
	double hcounts;
	double hcounts2;
};

Detector Detectors[2]; // DetID of -1 is LDet, DetID of 1 is UDet. So, in the index 0 is LDet, 1 is UDet
string PxToPrmp[128];
string PxToFET[128];
int *BctPx;
int *PxtBc;
//
vector<fitHisto> fitHistos;
vector<fitResults> pxFits;
TH1D *hsimSaves[50];
//
int maxPxSim;
int maxPxData;
bool spectraIni = false;
//
double gFano = 0.003; // fix it to a value for simplicity
//
int fitIndex = 0;
//
TF1 *HFit;
//
double mingain = 0.01;
double maxgain = 1.0;
string drawOn = "drawParts"; // drawOff, drawOn, drawParts (only the histoplots)

const int nCols = 13;
const int nRowPCol[nCols] = {7,8, 9,10, 11,12, 13, 12,11, 10,9, 8,7};

const double PI = acos(-1.0); // Definition of pi
const double hexSide = 5.2; // [mm] length of a hexagon's side. This is equal to 2*major diameter
const double hexhHeight = (hexSide)*sin(60.0/180.0*PI); // [mm] Half-height of a hexagon
const double WaferRad = (117.5/2.0); //mm A bit more than the diameter of effective area of the Nab Detector

void InitializePixel(HexPixel *px, int col, int row, int pxIndex, string BinLabel, string DetType) { // col is an index so it goes from 0 to 12

	double ytop = ((double)(nRowPCol[col]))*hexhHeight-hexhHeight;

	(*px).pxID = (pxIndex+1);
	// (*px).prID = PxtBc[(pxIndex+1)];
	(*px).PrBoard = PxToPrmp[pxIndex+1][0]-64.0;
	(*px).FETBoard = PxToFET[pxIndex+1][0]-64.0;

	(*px).center[0] = ((double)((col+1)-7))*1.5*hexSide;
	(*px).center[1] = ytop - ((double)(row))*2.0*hexhHeight;
	if(DetType == "LDet")
	{
		(*px).center[0] *= (-1.0);
		(*px).pxID += 1000.0;
	}
	string pxLab = ("#splitline{"+to_string(((int)((*px).pxID)))+"}{"+BinLabel+"}");

	(*px).radius = sqrt( ((*px).center[0])*((*px).center[0])+((*px).center[1])*((*px).center[1]) );
	(*px).angle = atan2((*px).center[1],(*px).center[0]);

	(*px).xEdges[0] = (*px).center[0]-hexSide/2.0;(*px).yEdges[0] = (*px).center[1]+hexhHeight;
	(*px).xEdges[1] = (*px).xEdges[0]+hexSide;(*px).yEdges[1] = (*px).yEdges[0];
	(*px).xEdges[2] = (*px).xEdges[1]+hexSide/2.0;(*px).yEdges[2] = (*px).yEdges[1]-hexhHeight;
	(*px).xEdges[3] = (*px).xEdges[2]-hexSide/2.0;(*px).yEdges[3] = (*px).yEdges[2]-hexhHeight;
	(*px).xEdges[4] = (*px).xEdges[3]-hexSide;(*px).yEdges[4] = (*px).yEdges[3];
	(*px).xEdges[5] = (*px).xEdges[4]-hexSide/2.0;(*px).yEdges[5] = (*px).yEdges[4]+hexhHeight;
	(*px).xEdges[6] = (*px).xEdges[0];(*px).yEdges[6] = (*px).yEdges[0];

	(*px).gr = new TGraph(6, (*px).xEdges, (*px).yEdges);
	(*px).grL = new TGraph(7, (*px).xEdges, (*px).yEdges);
	(*px).pxLabel = pxLab;
	(*px).gr->SetName((*px).pxLabel.c_str());

	(*px).nWF = 0.0;
}

void nullMapping() {

  for(int i=0;i<128;i++)
  {
    PxToPrmp[i] = "null";
    PxToFET[i] = "null";
  }
}

void ReadMapping() {

	string line,fetr,prmr;
	int pxr,brd,chn,bcr;

  ifstream myfile;
  myfile.open("./PixelFetPreampMap_V2.csv");
	getline(myfile,line);
  for(int j=0;j<131;j++)
  {
		myfile >> pxr >> fetr >> prmr >> brd >> chn >> bcr;
		if(pxr>0 && pxr < 128)
		{
			PxToPrmp[pxr] = prmr;
			PxToFET[pxr] = fetr;
		}
  }
  myfile.close();

}

void SetupHexPlot(Detector *DetIn, string DetType, string htitle = "Pixel - Preamp Map") {

	string hname;
	string hp1title,hp2title;
	if(DetType == "UDet")
	{
		hname = "UDet";
		hp2title = "Upper Detector | Pixel - FET Map";
	}
	if(DetType == "LDet")
	{
		hname = "LDet";
		hp2title = "Lower Detector | Pixel - FET Map";
	}
	hp1title = hname+" | "+htitle;
	(*DetIn).HexPlotPrmp = new TH2Poly(hname.c_str(),hp1title.c_str(),3,-WaferRad,WaferRad,3,-WaferRad,WaferRad); //Hex Plot for Singles Events

	int index = 0;
	for(int i=0;i<nCols;i++)
	{
		for(int j=0;j<nRowPCol[i];j++)
		{
			InitializePixel(&((*DetIn).pixlesPrmp[index]), i, j, index, PxToPrmp[index+1], DetType);
			(*DetIn).HexPlotPrmp->AddBin((*DetIn).pixlesPrmp[index].gr);
			index++;
		}
	}
}

void resetBins(Detector *Detin, double bweight) {

	int tempbin;
	for(int i=0;i<127;i++)
	{
		tempbin = (*Detin).HexPlotPrmp->FindBin((*Detin).pixlesPrmp[i].center[0],(*Detin).pixlesPrmp[i].center[1]);
		(*Detin).HexPlotPrmp->SetBinContent(tempbin, bweight);
		// printf("%s: bin: %d | content: %f\n",(hps->GetBinName(tempbin)),tempbin,bweight );
	}
}

// template histo fitting
// 0 = zero intercetp in unit of ADC
// 1 = gain in unit of ADC/ keV
// 2 = amplitude scaling [arb.]
Double_t templateFitter(Double_t *x, Double_t *par)
{
	double xeval = (x[0]*par[1])+par[0];
	if(xeval < fitHistos[fitIndex].intpMin || xeval > fitHistos[fitIndex].intpMax)
	{
		// printf("xeval: %lf\n", xeval);

		TF1::RejectPoint();
		return 0;
	}

	// double counts = simModel(xeval);
	double counts = fitHistos[fitIndex].simModel->Eval(xeval);

	return par[2]*counts;
}

void convolveGaussian(int histoID) {

	// Copying over the value of the sigma used
	fitHistos[histoID].sigma = pxFits[histoID].sigma;

	string hname = "hsg"+to_string(pxFits[histoID].pixel);
	string htitle = "Simulated Pixel "+to_string(pxFits[histoID].pixel)+" 207Bi spectrum representing Pixel "+to_string(pxFits[histoID].hchannel)+" with Gaus convolution;Energy [keV];Norm Counts";
	int nbins = fitHistos[histoID].hr->GetXaxis()->GetNbins();
	double ymin = fitHistos[histoID].hr->GetXaxis()->GetXmin();
	double ymax = fitHistos[histoID].hr->GetXaxis()->GetXmax();

	if(fitHistos[histoID].iniFlag) delete fitHistos[histoID].hg; // delete dumplicate histo if it has been initialized
	fitHistos[histoID].hg = new TH1F(hname.c_str(),htitle.c_str(), nbins, ymin, ymax);
	fitHistos[histoID].hg->SetDirectory(nullptr);

	double GausHRange = 100.0; // Half range of the gaus convolution [keV]
	double GausBin = (ymax-ymin)/((double)(nbins)); // Binning of gaus convolution [keV]
	int nHGaus = ((int)(GausHRange/GausBin)); // Round out the number of bins if half-range isn't compatible with histo binning
	GausHRange = ((double)(nHGaus))*GausBin;
	int nGaus = 2*nHGaus+1; // this ensures that nGaus is odd number

	double *hsimBins = new double[nbins];
	for(int i=0;i<nbins;i++) hsimBins[i] = 0.0;

	double pi = acos(-1.0);
	double Ee;
	double sigma;
	double histBin;
	double convDisp;
	double convCount;
	double sigmaRaw = pxFits[histoID].sigma;

	for(int i=1;i<=nbins;i++) // Looping through bins of hr, the raw histogram
	{
		// Center of the Gaussian convolution
		Ee = fitHistos[histoID].hr->GetBinCenter(i);
		histBin = fitHistos[histoID].hr->GetBinContent(i);
		sigma = sqrt(sigmaRaw*sigmaRaw + Ee*gFano);
		if(sigma == 0.0) sigma = 0.00000000000001; // Shouldn't happen often, but if the sigma is 0, at least make it non-zero
		hsimBins[i-1] += histBin*(exp(-0.5*(0.0/sigma)*(0.0/sigma))/(sqrt(2.0*pi)*sigma));

		// Looping over each side of Gaus convolution
		for(int j=1;j<=nHGaus;j++)
		{
			convDisp = ((double)(j))*GausBin;
			convCount = histBin*(exp(-0.5*(convDisp/sigma)*(convDisp/sigma))/(sqrt(2.0*pi)*sigma));
			if((i+j) > 0 && (i+j) <= nbins) hsimBins[(i+j)-1] += convCount;
			if((i-j) > 0 && (i-j) <= nbins) hsimBins[(i-j)-1] += convCount;
		}
	}

	// dummy vectors to populate the interpolator
	vector<double> xvvals;
	vector<double> simvals;
	for(int i=1;i<=nbins;i++) // After the convolved histogram has been fully generated, loop back to populate
	{
		fitHistos[histoID].hg->SetBinContent(i, hsimBins[i-1]);
		fitHistos[histoID].hg->SetBinError(i, sqrt(hsimBins[i-1]));

		simvals.push_back(hsimBins[i-1]);
		xvvals.push_back(fitHistos[histoID].hg->GetBinCenter(i));
	}

	// TCanvas *c5 = new TCanvas("c5", "Canvas",200, 10, 850, 752);
	// c5->SetGrid();
	// c5->SetLogy();
	// gStyle->SetOptFit(111);
	// gStyle->SetOptStat(111);
	// fitHistos[histoID].hg->Draw("HIST");
	// fitHistos[histoID].hg->GetXaxis()->SetRangeUser(0.0, 200.0);
	// fitHistos[histoID].hg->GetYaxis()->SetRangeUser(0.001, 5000.0);
	// c5->SaveAs(("./simHisto"+to_string(histoID)+".png").c_str());
	// delete c5;

	if(fitHistos[histoID].iniFlag) delete fitHistos[histoID].simModel;

	fitHistos[histoID].simModel = new ROOT::Math::Interpolator(simvals.size(), ROOT::Math::Interpolation::kCSPLINE);
	fitHistos[histoID].simModel->SetData(xvvals,simvals);
	fitHistos[histoID].intpMin = xvvals[0];
	fitHistos[histoID].intpMax = xvvals[xvvals.size()-1];

	// printf("%d %lf %lf\n", histoID, fitHistos[histoID].intpMin, fitHistos[histoID].intpMax);

	delete [] hsimBins;

	fitHistos[histoID].iniFlag = true;
}

void pullSimHisto(string fname, string fCenter) {


	if(spectraIni) // Clean out the simulation histos if there are more than 1 simulations being processed
	{
		for(int i=0;i<fitHistos.size();i++)
		{
			delete fitHistos[i].hr;
			delete fitHistos[i].hg;
			delete fitHistos[i].simModel;
		}
		fitHistos.clear();

		for(int i=0;i<fitHistos.size();i++) delete hsimSaves[i];
	}

	double EMinCut = 20.0;
	double EMaxCut = 80.0;
	//
	TH1D *htemp;
	double hIntg;
	double maxCount = 0.0;

	// Open the file without any source displacement to decide which pixel to pull
	TFile *f2 = new TFile(fCenter.c_str());
	TH2F *hd2 = (TH2F*)f2->Get("hdum");
	for(int i=129;i<=255;i++)
	{
		htemp = hd2->ProjectionY("_py", i, i);
		hIntg = htemp->Integral(htemp->FindBin(EMinCut), htemp->FindBin(EMaxCut));
		if(maxCount < hIntg)
		{
			maxCount = hIntg;
			maxPxSim = i-128;
		}
		delete htemp;
	}
	f2->Close();

	// Open the file without any source displacement to decide which pixel to pull
	f2 = new TFile(fname.c_str());
	hd2 = (TH2F*)f2->Get("hdum");

	int repIndex;
	double repContent,lMax;
	int repHisto;
	int hindeces[50] = {-1,-1,-1,-1};
	double hcontents[50] = {-1.0,-1.0,-1.0,-1.0};
	for(int i=0;i<50;i++)
	{
		hindeces[i] = -1;
		hcontents[i] = -1.0;
	}

	for(int i=129;i<=255;i++)
	{
		htemp = hd2->ProjectionY("_py", i, i);
		hIntg = htemp->Integral(htemp->FindBin(EMinCut), htemp->FindBin(EMaxCut));

		repIndex = -1;
		repContent = 10000000000000000.0;
		for(int j=0;j<50;j++)
		{
			if(hIntg > hcontents[j])
			{
				if(repContent > hcontents[j])
				{
					repIndex = j;
					repContent = hcontents[j];
					repHisto = i;
					lMax = hIntg;
				}
			}
		}

		if(repIndex != -1)
		{
			hindeces[repIndex] = repHisto;
			hcontents[repIndex] = lMax;
		}

		delete htemp;
	}

	for(int i=0;i<4;i++)
	{
		hsimSaves[i] = hd2->ProjectionY("_py", hindeces[i], hindeces[i]);
		hsimSaves[i]->SetDirectory(nullptr);
		hsimSaves[i]->SetName(("hsave"+to_string(hindeces[i]-128)).c_str());
	}
	f2->Close();

	// Open the sim file with targeted displacement given by the initial argument
	f2 = new TFile(fname.c_str());
	hd2 = (TH2F*)f2->Get("hdum");

	int nbins;
	fitHisto tempHistos[pxFits.size()];

	// printf("Pixel with Most Counts in Simulation = %d\n", maxPxSim);
	// printf("Pixel with Most Counts in Data = %d\n", maxPxData);
	for(int i=0;i<pxFits.size();i++)
	{
		double xTar = Detectors[1].pixlesPrmp[pxFits[i].pixel-1].center[0]-Detectors[1].pixlesPrmp[maxPxData-1].center[0]+Detectors[1].pixlesPrmp[maxPxSim-1].center[0];
		double yTar = Detectors[1].pixlesPrmp[pxFits[i].pixel-1].center[1]-Detectors[1].pixlesPrmp[maxPxData-1].center[1]+Detectors[1].pixlesPrmp[maxPxSim-1].center[1];
		int pxTar = Detectors[1].HexPlotPrmp->FindBin(xTar, yTar);
		// printf("Data Pixel: %d | Simulation Pixel: %d\n", pxFits[i].pixel, pxTar);
		pxFits[i].sigma = 1.5;
		pxFits[i].hchannel = pxTar+128;

		tempHistos[i].hr = hd2->ProjectionY(("_py"+to_string(pxFits[i].pixel)).c_str(), pxFits[i].hchannel, pxFits[i].hchannel);
		tempHistos[i].hr->SetDirectory(nullptr);
		tempHistos[i].hr->SetName(("hsr"+to_string(pxFits[i].pixel)).c_str());
		tempHistos[i].hr->SetTitle(("Simulated Pixel "+to_string(pxTar)+" 207Bi spectrum representing Pixel "+to_string(pxFits[i].pixel)+";Energy [keV];Norm Counts").c_str());

		nbins = tempHistos[i].hr->GetXaxis()->GetNbins();
		tempHistos[i].hcounts = 0.0;
		tempHistos[i].hcounts2 = 0.0;
		for(int j=1;j<=nbins;j++)
		{
			tempHistos[i].hcounts += (tempHistos[i].hr->GetBinContent(j));
			tempHistos[i].hcounts2 += (tempHistos[i].hr->GetBinContent(j))*(tempHistos[i].hr->GetBinContent(j));
		}

		tempHistos[i].iniFlag = false;
		tempHistos[i].fname = fname;

		fitHistos.push_back(tempHistos[i]);
		convolveGaussian(i);

		if(drawOn == "drawOn")
		{
			TCanvas *c5 = new TCanvas("c5", "Canvas",200, 10, 850, 752);
			c5->SetGrid();
			c5->SetLogy();
			fitHistos[i].hg->Draw("HIST");
			fitHistos[i].hg->GetXaxis()->SetRangeUser(0.0, 200.0);
			fitHistos[i].hg->GetYaxis()->SetRangeUser(0.0001, 50000.0);
			c5->Update();
			c5->SaveAs(("./SimHisto"+to_string(pxFits[i].pixel)+".png").c_str());

			delete c5;
		}
	}
	f2->Close();

	spectraIni = true;
}

void fitPixel(int tarPx) {

	// fitIndex is a global variable that set the spectra for the fitter.
	// tarPx is meant to pass the target pixel's fit index that is being fitted

	//----------------------------------
	// In principle, drawing isn't necessary to do the fit, but it might here
	TCanvas *c5;
	if(drawOn == "drawOn")
	{
		c5 = new TCanvas("c5", "Canvas",200, 10, 850, 752);
		c5->SetGrid();
		c5->SetLogy();
		pxFits[tarPx].hrun->Draw("HIST");
	}
	//----------------------------------

	double lchi2;
	pxFits[tarPx].fitIni = false;
	pxFits[tarPx].chi2 = 1000000000000.0;

	double lpars[3];
	if(pxFits[tarPx].iniFlag) for(int i=0;i<3;i++) lpars[i] = pxFits[tarPx].pars[i];
	else
	{
		lpars[0] = 0.0;
		lpars[1] = 0.33;
		lpars[2] = 0.1;
	}
	pxFits[tarPx].iniFlag = true;

	double minsig = 0.1;
	double maxsig = 10.0;
	double sigstep = 0.1;
	int nsig = (maxsig-minsig)/sigstep + 1;
	double lsigma,bestFitSigma;

	for(int i=0;i<nsig;i++) // sigma loop
	{
		lsigma = minsig + ((double)(i))*sigstep;

		pxFits[tarPx].sigma = lsigma;
		pxFits[fitIndex].sigma = lsigma;
		convolveGaussian(fitIndex);

		HFit->SetParameters(lpars[0],lpars[1],lpars[2]);
		for(int j=0;j<4;j++) pxFits[tarPx].hrun->Fit(HFit, "QRL");
		for(int j=0;j<4;j++) pxFits[tarPx].hrun->Fit(HFit, "QRML");

		lchi2 = (HFit->GetChisquare());

		if(pxFits[tarPx].chi2 > lchi2 && !isnan(lchi2))
		{
			pxFits[tarPx].iniFlag = true;
			pxFits[tarPx].chi2 = lchi2;
			bestFitSigma = lsigma;

			for(int j=0;j<3;j++) pxFits[tarPx].pars[j] = (HFit->GetParameter(j));
		}
	}

	// Setting the initializers to the best-fit values from prior sweep
	// printf("First pass: Fit Sigma = %lf\n", bestFitSigma);
	// for(int j=0;j<3;j++) printf("Fit par %d:%lf\n", j, pxFits[tarPx].pars[j]);

	if(pxFits[tarPx].iniFlag) for(int i=0;i<3;i++) lpars[i] = pxFits[tarPx].pars[i];

	// Making a finer sweep across the detector resolution
	sigstep = 0.05;
	minsig = bestFitSigma-sigstep*10.0;
	maxsig = bestFitSigma+sigstep*10.0;
	if(minsig < 0.0) minsig = 0.05;
	if(maxsig > 10.0) maxsig = 10.0;
	nsig = (maxsig-minsig)/sigstep + 1;

	pxFits[tarPx].fitIni = false;
	pxFits[tarPx].chi2 = 1000000000000.0;

	for(int i=0;i<nsig;i++) // sigma loop
	{
		lsigma = minsig + ((double)(i))*sigstep;

		pxFits[tarPx].sigma = lsigma;
		pxFits[fitIndex].sigma = lsigma;
		convolveGaussian(fitIndex);

		HFit->SetParameters(lpars[0],lpars[1],lpars[2]);
		for(int j=0;j<4;j++) pxFits[tarPx].hrun->Fit(HFit, "QRL");
		for(int j=0;j<4;j++) pxFits[tarPx].hrun->Fit(HFit, "QRML");

		lchi2 = (HFit->GetChisquare());

		if(pxFits[tarPx].chi2 > lchi2 && !isnan(lchi2))
		{
			pxFits[tarPx].iniFlag = true;
			pxFits[tarPx].chi2 = lchi2;
			bestFitSigma = lsigma;

			for(int j=0;j<3;j++) pxFits[tarPx].pars[j] = (HFit->GetParameter(j));
			for(int j=0;j<3;j++) pxFits[tarPx].parErrs[j] = (HFit->GetParError(j));
		}
	}
	// Setting the initializers to the best-fit values from prior sweep
	// printf("Final pass: Fit Sigma = %lf\n", bestFitSigma);
	// for(int j=0;j<3;j++) printf("Fit par %d:%lf\n", j, pxFits[tarPx].pars[j]);
	// if(!(pxFits[tarPx].iniFlag)) printf("Fit has Failed\n");

	pxFits[tarPx].sigma = bestFitSigma;
	pxFits[fitIndex].sigma = bestFitSigma;
	convolveGaussian(fitIndex);

	if(drawOn == "drawOn")
	{
		pxFits[tarPx].hrun->Fit(HFit, "QRL");
		HFit->Draw("SAME");
		gStyle->SetOptFit(111);
		gStyle->SetOptStat(111);
		c5->Update();
		c5->SaveAs(("./FitPixel"+to_string(tarPx)+".png").c_str());
		delete c5;
	}

}

double returnCombinedChi2(string paramOut) {

	// Resetting the main Fitter settings, in case it hasn't be reset previously
	HFit->SetParLimits(0, -100.0, 100.0);
	HFit->SetParLimits(1, mingain, maxgain);
	HFit->ReleaseParameter(2);

	vector<double> sigmaSave;

	// par-fitting the spectra individually to
	for(int i=0;i<pxFits.size();i++)
	{
		if(pxFits[i].hcounts > 0.0)
		{
			fitIndex = i;
			fitPixel(i);
			// for(int j=0;j<pxFits.size();j++)
			// {
			// 	fitIndex = j;
			// 	fitPixel(i);
			// }
		}
		sigmaSave.push_back(pxFits[i].sigma);
	}

	//----------------------------------------------------------------------------
	// Leftover from previous version. It won't run as-is
	// if(drawOn == "drawOn")
	// {
	// 	TCanvas *ctemp = new TCanvas("ctemp", "Canvas",200, 10, 850, 752);
	// 	ctemp->SetGrid();
	// 	ctemp->SetLogy();
	// 	pixel76.hrun->Draw("HIST");
	// 	HFit->Draw("SAME");
	// 	// ctemp->SaveAs(("./best_AllFreeFit_px"+to_string(pixel76.pixel)+"_rad_"+to_string(radint)+"mm.png").c_str());
	// }
	//----------------------------------------------------------------------------

	int nNormDiv = 600;
	double gmax = -10000000.0;
	double gmin = 10000000.0;
	for(int i=0;i<pxFits.size();i++)
	{
		// printf("%lf\n", pxFits[i].pars[2]);
		if(pxFits[i].hcounts > 0.0 && !(isnan(pxFits[i].pars[2])) && !(isinf(pxFits[i].pars[2])) && pxFits[i].pars[2] > 0.0)
		{
			if(gmax < pxFits[i].pars[2]) gmax = pxFits[i].pars[2];
			if(gmin > pxFits[i].pars[2]) gmin = pxFits[i].pars[2];
		}
	}
	if(gmax == -10000000.0) gmax = 100.0;
	if(gmin == 10000000.0) gmin = 0.0001;

	gmax *= 10.0;
	gmin /= 10.0;
	double multp = exp(log(gmax/gmin)/((double)(nNormDiv)));

	// printf("gmax: %lf, gmin: %lf, multp: %lf\n", gmax, gmin, multp);

	TFile *tfout;
	TCanvas *ctemp2;
	TGraph *grchi;
	TF1 *fpol2;

	double gnorm = gmin;

	double pchi2;
	double gchi2 = 0.0;

	int gNormMin;
	double normDiv = 0.8;
	double gNormd = -1.0;
	double gchi2Min = 100000000000.0;

	vector<double> gs;
	vector<double> chi2s;

	vector<string> outputs;

	string normMethod = "BisectionMethod";
	if(normMethod == "BisectionMethod")
	{
		int nGrid = 50;

		nNormDiv = 50;
		bool GridLevel2 = false;

		for(int k=0;k<nGrid;k++)
		{
			if(gNormd > 0.0)
			{
				gmax /= 10.0;
				gmin *= 10.0;

				if(gmin > gNormd || gmax < gNormd || GridLevel2)
				{
					GridLevel2 = true;

					gmax = gNormd*(1.0+normDiv);
					gmin = gNormd*(1.0-normDiv);

					normDiv *= 0.8;
				}
			}
			multp = exp(log(gmax/gmin)/((double)(nNormDiv)));
			outputs.push_back(to_string(k)+": ["+to_string(gmin)+", "+to_string(gmax)+"] | "+to_string(gNormd));

			gnorm = gmin;
			for(int i=0;i<nNormDiv;i++) // This sweeps across ~0.017x to 10x of the maximum normalization
			{
				// if(i%50 == 0) printf("%d\n", i);
				gchi2 = 0.0;

				for(int j=0;j<pxFits.size();j++)
				{
					if(pxFits[j].hcounts == 0.0) continue;

					if(fitHistos[j].hcounts < 1000.0)
					{
						pchi2 = pxFits[j].hcounts2;
					}
					else
					{
						fitIndex = j;
						for(int k=0;k<2;k++) HFit->SetParameter(k, pxFits[j].pars[k]);
						HFit->FixParameter(2, gnorm);
						for(int k=0;k<10;k++) pxFits[j].hrun->Fit(HFit, "QRML");
						pchi2 = HFit->GetChisquare();
					}

					if(!isnan(pchi2) && !isinf(pchi2)) gchi2 += (pchi2);
				}

				if(gchi2 != 0.0)
				{
					gs.push_back(gnorm);
					chi2s.push_back((gchi2));

					if(gchi2Min > gchi2)
					{
						gchi2Min = gchi2;
						gNormd = gnorm;
					}
				}

				gnorm *= multp;
			}
		}

		if(drawOn == "drawOn" || drawOn == "drawParts")
		{
			string pngOut = paramOut;
			pngOut.erase( pngOut.end()-4 );
			pngOut.erase(0,8);
			tfout = new TFile(("./plots/"+pngOut+"_histoplots.root").c_str(),"recreate");
		}

		grchi = new TGraph(gs.size(), &gs[0], &chi2s[0]);
		if(drawOn == "drawOn" || drawOn == "drawParts")
		{
			ctemp2 = new TCanvas("ctemp2", "Canvas",200, 10, 850, 752);
			ctemp2->SetGrid();

			grchi->Draw("AP");
		}

		// if(drawOn == "drawOn" || drawOn == "drawParts") ctemp2->SaveAs("./chi2plot.png");

		if(drawOn == "drawOn" || drawOn == "drawParts")
		{
			tfout->cd();
			grchi->Write();
		}

		gnorm = gNormd;

		// for(int k=0;k<nGrid;k++) printf("%s\n", outputs[k].c_str());
	}
	if(normMethod == "QuadFit")
	{
		for(int i=0;i<nNormDiv;i++) // This sweeps across ~0.017x to 10x of the maximum normalization
		{
			// if(i%50 == 0) printf("%d\n", i);
			gchi2 = 0.0;
			gs.push_back(gnorm);

			for(int j=0;j<pxFits.size();j++)
			{
				if(pxFits[j].hcounts == 0.0) continue;

				fitIndex = j;
				for(int k=0;k<2;k++) HFit->SetParameter(k, pxFits[j].pars[k]);
				HFit->FixParameter(2, gnorm);
				for(int k=0;k<10;k++) pxFits[j].hrun->Fit(HFit, "QRML");
				pchi2 = HFit->GetChisquare();
				if(!isnan(pchi2)) gchi2 += (pchi2);
			}
			chi2s.push_back((gchi2));

			if(gchi2Min > gchi2)
			{
				gchi2Min = gchi2;
				gNormMin = i;
			}

			gnorm *= multp;
		}

		double fitMin,fitMax;
		if(gNormMin-5 >= 0) fitMin = gs[gNormMin-5];
		else fitMin = gs[0];
		if(gNormMin+5 < nNormDiv) fitMax = gs[gNormMin+5];
		else fitMax = gs[nNormDiv-1];

		if(drawOn == "drawOn" || drawOn == "drawParts")
		{
			tfout = new TFile("./histoplots.root","recreate");
		}

		fpol2 = new TF1("fpol2","pol2", fitMin, fitMax);

		grchi = new TGraph(gs.size(), &gs[0], &chi2s[0]);
		if(drawOn == "drawOn" || drawOn == "drawParts")
		{
			ctemp2 = new TCanvas("ctemp2", "Canvas",200, 10, 850, 752);
			ctemp2->SetGrid();

			grchi->Draw("AP");
		}

		grchi->Fit(fpol2, "QR");
		gnorm = -(fpol2->GetParameter(1))/2.0/(fpol2->GetParameter(2));
		// if(drawOn == "drawOn" || drawOn == "drawParts") ctemp2->SaveAs("./chi2plot.png");

		if(drawOn == "drawOn" || drawOn == "drawParts")
		{
			tfout->cd();
			grchi->Write();
		}
	}

	// Fitting it the last time with best-fit normalization.
	//This is mostly repeating a sigle point in the previous sweep to pull the fit results
	gchi2 = 0.0;
	for(int i=0;i<pxFits.size();i++)
	{
		if(pxFits[i].hcounts == 0.0) continue;

		if(fitHistos[i].hcounts < 1000.0)
		{
			pxFits[i].chi2 = pxFits[i].hcounts2;
			pxFits[i].NDF = -1.0;
			for(int j=0;j<2;j++) pxFits[i].pars[j] = -1.0;
			for(int j=0;j<2;j++) pxFits[i].parErrs[j] = -1.0;

			pchi2 = pxFits[i].hcounts2;
		}
		else
		{
			fitIndex = i;
			for(int j=0;j<2;j++) HFit->SetParameter(j, pxFits[i].pars[j]);
			HFit->FixParameter(2, gnorm);
			pxFits[i].hrun->Fit(HFit, "QRML");
			//
			pxFits[i].chi2 = HFit->GetChisquare();
			pxFits[i].NDF = HFit->GetNDF();
			for(int j=0;j<2;j++) pxFits[i].pars[j] = (HFit->GetParameter(j));
			for(int j=0;j<2;j++) pxFits[i].parErrs[j] = (HFit->GetParError(j));
			//
			pchi2 = HFit->GetChisquare();
		}

		if(!isnan(pchi2)) gchi2 += (pchi2);
		if(drawOn == "drawOn" || drawOn == "drawParts")
		{
			TCanvas *ctemp = new TCanvas("ctemp", "Canvas",200, 10, 850, 752);
			ctemp->SetGrid();
			ctemp->SetLogy();
			pxFits[i].hrun->Draw("HIST");
			HFit->Draw("SAME");
			// ctemp->SaveAs(("./best_freeFit_px"+to_string(pixel76.pixel)+"_rad_"+to_string(radint)+"mm.png").c_str());

			tfout->cd();
			pxFits[i].hrun->Write();
			fitHistos[i].hr->Write();
		}
	}

	FILE* fp;
	fp = fopen(paramOut.c_str(),"w");
	fprintf(fp,"Input_spectra_name: %s\n", (pxFits[0].fname).c_str());
	fprintf(fp,"Simulation_spectra_name: %s\n", (fitHistos[0].fname).c_str());
	fprintf(fp,"Global_chi2: %lf\n", gchi2Min);
	fprintf(fp,"Global_normalization: %lf\n", gnorm);
	fprintf(fp,"Pixel, simChannel, E0, E0_err, gain, gain_err, sigma/gaus_width, pixelChi2, pixelNDF\n");
	for(int i=0;i<pxFits.size();i++)
	{
		if(pxFits[i].hcounts == 0.0)
		{
			fprintf(fp,"%d %d -1 -1 -1 -1 -1 -1 -1\n", pxFits[i].pixel, pxFits[i].hchannel);
		}
		else
		{
			fprintf(fp,"%d %d ", pxFits[i].pixel, pxFits[i].hchannel);
			for(int j=0;j<2;j++) fprintf(fp,"%lf %lf ", pxFits[i].pars[j], pxFits[i].parErrs[j]);
			fprintf(fp,"%lf %lf %lf\n", pxFits[i].sigma, pxFits[i].chi2, pxFits[i].NDF);
		}
	}
	fclose(fp);

	HFit->ReleaseParameter(2);
	if(drawOn == "drawOn" || drawOn == "drawParts")
	{
		for(int i=0;i<4;i++) hsimSaves[i]->Write();
		tfout->Close();
		delete ctemp2;
	}

	if(normMethod == "QuadFit") delete fpol2;
	delete grchi;

	return (gchi2);
}

int readSpectra(string fname) {

	string line;
	string substr;
	vector<string> v;
	stringstream ss;
	string newline;
	//
	int ncol;
	vector<int> pxs;

	// Opening the input spectra
	ifstream specFile;
  specFile.open(("./InputSpectra/"+fname).c_str());

	// parsing the comma delimited header
	getline(specFile,line);
	ss << line;
	while (ss.good())
	{
    getline(ss, substr, ',');
    v.push_back(substr);
  }
	for(int i=0;i<v.size();i++)
	{
		newline = "";
		for(int j=0;j<v[i].size();j++) if(48 <= v[i][j] && v[i][j] <= 57) newline += v[i][j];

		if(newline.size() > 0) pxs.push_back(stoi(newline));
	}

	int ErrorFlag = 0;
	int nbins = 0;
	int maxEngBin;
	double strD;
	vector<double> spectra[pxs.size()+1];

	// reading spectra
	while(getline(specFile,line))
	{
		ss.str("");ss.clear();v.clear();

		ss << line;
		while (ss.good())
		{
			string substr;
			getline(ss, substr, ',');
			v.push_back(substr);
		}
		if(v.size() != (pxs.size()+1) && ErrorFlag == 0)
		{
			printf("PARSING ERROR!!!\n");
			ErrorFlag = 1; // print this only once, since it's in a while loop
		}
		for(int i=0;i<v.size();i++)
		{
			strD = stod(v[i]);
			spectra[i].push_back(strD);
			if(i != 0 && strD != 0.0) maxEngBin = nbins;
		}
		nbins++;
	}
  specFile.close();

	double binning = spectra[0][1]-spectra[0][0];
	double minEng = spectra[0][0]-binning/2.0;
	double maxEng = spectra[0][maxEngBin]+binning/2.0;
	nbins = ((int)((maxEng-minEng)/((double)(binning))));

	fitResults tempFit[pxs.size()];
	for(int i=0;i<pxs.size();i++)
	{
		tempFit[i].fname = fname;
		//
		tempFit[i].BC = -1;
		tempFit[i].pixel = pxs[i];
		tempFit[i].hchannel = -1;
		tempFit[i].findex = -1;
		//
		tempFit[i].chi2 = -1.0;
		tempFit[i].NDF = -1.0;
		//
		tempFit[i].iniFlag = false;
		tempFit[i].fitIni = false;
		tempFit[i].sigma = -1.0;
		//
		for(int j=0;j<3;j++) tempFit[i].pars[j] = -1.0;
		for(int j=0;j<3;j++) tempFit[i].parErrs[j] = -1.0;
		//
		tempFit[i].EMax = maxEng;
		tempFit[i].hcounts = -1.0;
		//
		tempFit[i].hrun = new TH1F(("hpx_"+to_string(pxs[i])).c_str(), ("Histogram of Pixel "+to_string(pxs[i])+";Energy;Counts").c_str(), nbins, minEng, maxEng);
		for(int j=0;j<maxEngBin;j++)
		{
			if(spectra[i+1][j] != 0.0)
			{
				tempFit[i].hrun->SetBinContent((tempFit[i].hrun->FindBin(spectra[0][j])), spectra[i+1][j]);
				tempFit[i].hrun->SetBinError((tempFit[i].hrun->FindBin(spectra[0][j])), sqrt(spectra[i+1][j]));
			}
			// else
			// {
			// 	tempFit[i].hrun->SetBinContent((tempFit[i].hrun->FindBin(spectra[0][j])), 0.0);
			// 	tempFit[i].hrun->SetBinError((tempFit[i].hrun->FindBin(spectra[0][j])), 0.01);
			// }
		}
		pxFits.push_back(tempFit[i]);
	}

	// // Dumping out the read-in spectra as debuggin
	// printf("pixels:");
	// for(int i=0;i<pxs.size();i++) printf("%d ", pxs[i]);
	// printf("\n");
	// //
	// for(int i=0;i<spectra[0].size();i++)
	// {
	// 	for(int j=0;j<(pxs.size()+1);j++) printf("%lf ", spectra[j][i]);
	// 	printf("\n");
	// }

	double EMin = 50.0;
	int binMin = pxFits[0].hrun->FindBin(EMin);
	int binMax = pxFits[0].hrun->FindBin(maxEng);

	double xCoor,yCoor;
	double xCoorAvg = 0.0;
	double yCoorAvg = 0.0;
	double Norm = 0.0;
	double maxCount = 0.0;
	for(int i=0;i<pxFits.size();i++)
	{
		pxFits[i].hcounts = 0.0;
		pxFits[i].hcounts2 = 0.0;
		for(int j=binMin;j<=binMax;j++)
		{
			pxFits[i].hcounts += (pxFits[i].hrun->GetBinContent(j));
			pxFits[i].hcounts2 += (pxFits[i].hrun->GetBinContent(j))*(pxFits[i].hrun->GetBinContent(j));
		}

		if(maxCount < pxFits[i].hcounts)
		{
			maxCount = pxFits[i].hcounts;
			maxPxData = pxFits[i].pixel;
		}

		xCoor = Detectors[1].pixlesPrmp[pxFits[i].pixel-1].center[0];
		yCoor = Detectors[1].pixlesPrmp[pxFits[i].pixel-1].center[1];

		xCoorAvg += (pxFits[i].hcounts)*xCoor;
		yCoorAvg += (pxFits[i].hcounts)*yCoor;

		Norm += pxFits[i].hcounts;
	}
	xCoorAvg /= Norm;
	yCoorAvg /= Norm;

	double effRad = sqrt(xCoorAvg*xCoorAvg + yCoorAvg*yCoorAvg)/1.15; // 1.15 is to account for the difference in the magnitude of the magnetic field
	int approxRad = ((int)(round(effRad/5.0))*5);

	//for 207Bi data, radial options range from 0-30 in steps of 5
	if(approxRad > 30) approxRad = 30;

	return approxRad;
}

void processChi2s(string inputName, int Mylar, int approxRad, string carrRad) {

	double minchi2 = 1000000000.0;
	double minX, minY;

	double tarPx;

	string line;
	string partName;
	string dumread;
	double XDisp;
	double YDisp;
	double chi2;

	vector<double> xvals;
	vector<double> yvals;
	vector<double> zvals;

	string substr1,substr2;
	string shortstr;
	string tarCut = "_shift";
	string preCut = "NabSimulation_Output";

	FILE* fpMerge;
	fpMerge = fopen(("./Analysis_results/"+inputName+"_"+to_string(Mylar)+"umMylar_"+to_string(approxRad)+"mmXSimOffset_"+carrRad+"mmRad_"+to_string(XDisp)+"mmX_"+to_string(YDisp)+"mmY_MergedResult.txt").c_str(),"w");

	FILE* fp;
	fp = fopen("./parts/missingRuns.txt","a");

	bool fileFlag = false;
	bool writeFlag = false;

	// Loop through the grid points to track the lowest chi2
	for(int i=0;i<28;i++)
	{
		// XDisp = -5.5+((double)(i))*0.5;
		XDisp = -5.4+((double)(i))*0.4;
		for(int j=0;j<28;j++)
		{
			// YDisp = -5.5+((double)(j))*0.5;
			YDisp = -5.4+((double)(j))*0.4;
			partName = "./parts/"+to_string(Mylar)+"umMylar"+carrRad+"mmCarrierRad/"+inputName+"_"+to_string(Mylar)+"umMylar_"+to_string(approxRad)+"mmXSimOffset_"+carrRad+"mmRad_"+to_string(XDisp)+"mmX_"+to_string(YDisp)+"mmY.txt";

			ifstream f(partName.c_str());

			if(f.good())
			{
				getline(f,line);
				f >> substr1 >> substr2;
				if(!fileFlag)
				{
					size_t pos = substr2.find(tarCut);
					shortstr = substr2.substr(0, pos);

					size_t pos2 = shortstr.find(preCut);
					shortstr.erase(0, pos2);

					fileFlag = true;

					printf("%s %s %s\n", substr1.c_str(), substr2.c_str(), shortstr.c_str());
				}
				f >> dumread >> chi2;
				getline(f,line);
				getline(f,line);
				getline(f,line);
				getline(f,line);
				getline(f,line);
				getline(f,line);
				f >> dumread >> tarPx;
				f.close();

				if(chi2 > minchi2)
				{
					minchi2 = chi2;
					minX = XDisp;
					minY = YDisp;
				}

				// if(chi2 < 3000)
				// {
					xvals.push_back(XDisp);
					yvals.push_back(YDisp);
					// zvals.push_back(tarPx-129.0);
					zvals.push_back(chi2);
				// }

				if(fileFlag && !writeFlag)
				{
					fprintf(fpMerge,"%s\n", shortstr.c_str());
					writeFlag = true;
				}

				printf("%lf %lf %lf %lf\n", chi2, (tarPx-129.0), XDisp, YDisp);
				fprintf(fpMerge,"%lf %lf %lf %lf\n", chi2, (tarPx-129.0), XDisp, YDisp);
			}
			else
			{
				fprintf(fp,"%s %d %s\n", inputName.c_str(), Mylar, carrRad.c_str());
			}
		}
	}
	fclose(fp);
	fclose(fpMerge);

	TGraph2D *gr2d = new TGraph2D(xvals.size(), &xvals[0], &yvals[0], &zvals[0]);

	TFile *tfout = new TFile(("./Analysis_results/chi2plot2D"+to_string(Mylar)+"umMylar"+carrRad+"mmCarrRad_histoplots.root").c_str(),"recreate");
	tfout->cd();
	gr2d->Write();
	tfout->Close();


}

int main(int argc, char *argv[]) {

	// note, for the 207Bi data, the full grid is from -5.4 to 5.4. There are additional spectra at y=5.5 and y=5.6 but they are not included in the full grid
	// also the grid span was changed from steps of 0.5 to steps of 0.4, and thus the number of cpus requested changed from 23 to 28
	//so where there is a 28, there used to be a 23

	gStyle->SetOptFit(111);
	gStyle->SetOptStat(111);

	if(argc < 4)
	{
		printf("ERROR: Not enough number of arguments. {analysisMode, name of input spectrum, mylar thickness, carrier radius, Displacement in x-axis (only for analysisMode = Production)}\n");
		return 0;
	}

	// this spectra is a
	string anlysisMode = argv[1];
	string inputSpectrum = argv[2];
	//
	int Mylar = (Int_t)atof(argv[3]); // Thickness of Mylar foil
	string carrRad = argv[4]; // Radius of the carrier
	int XDispIndex = -1;
	if(argc > 4) XDispIndex = (Int_t)atof(argv[5]); // index for the X-displacement // This doesn't get passed if the runmode is postMerge
	// double XDisp = -5.5+((double)(XDispIndex))*0.5; // X-displacement
	double XDisp = -5.4+((double)(XDispIndex))*0.4; // X-displacement

	ReadMapping();
	SetupHexPlot(&Detectors[0],"LDet");
	SetupHexPlot(&Detectors[1],"UDet");
	resetBins(&Detectors[0],0.0);
	resetBins(&Detectors[1],0.0);

	int approxRad;
	approxRad = readSpectra(inputSpectrum);

	// Running the mergers
	if(anlysisMode == "postMerge")
	{
		processChi2s(inputSpectrum, Mylar, approxRad, carrRad);
		return 0;
	}

	string processedSpectraFolder = "processedSpectra"+to_string(Mylar)+"umMylar"+carrRad+"mmCarrierRad/";

	// Initializing the main fitter
	string simSpectraCenter = "/storage/ideas/is-ajezghani3-0/SourceSimulation_Outputs/207Bi/processedSpectra/"+processedSpectraFolder+"NabSimulation_Output_CAL2702_207Bi_Sweep_"+to_string(Mylar)+"umMylar_"+to_string(approxRad)+".mmX_0.mmY_"+carrRad+"mmRad_1.umMaxStep_emlivermore_1.umCut_shift"+to_string(0.0)+"mmX_"+to_string(0.0)+"mmY_parsedHisto_0.root";
	string simSpectra = "/storage/ideas/is-ajezghani3-0/SourceSimulation_Outputs/207Bi/processedSpectra/"+processedSpectraFolder+"NabSimulation_Output_CAL2702_207Bi_Sweep_"+to_string(Mylar)+"umMylar_"+to_string(approxRad)+".mmX_0.mmY_"+carrRad+"mmRad_1.umMaxStep_emlivermore_1.umCut_shift"+to_string(XDisp)+"mmX_"+to_string(-5.4)+"mmY_parsedHisto_0.root";
	pullSimHisto(simSpectra, simSpectraCenter);

	HFit = new TF1("HFit", templateFitter, 50.0, 3240.0, 3);
	HFit->SetNpx(50000);
	HFit->SetParLimits(0, -100.0, 100.0);
	HFit->SetParLimits(1, mingain, maxgain);

	string outName;
	double chiDummy;
	// double YDisp = -5.5;
	double YDisp = -5.4;

	// Override to re-do missing runs when extra argument was passed
	if(argc > 6 && anlysisMode == "prodMakeUp")
	{
		// YDisp = -5.5+(atof(argv[6]))*0.5; // Y-displacement
		YDisp = -5.4+(atof(argv[6]))*0.4; // Y-displacement

		simSpectra = "/storage/ideas/is-ajezghani3-0/SourceSimulation_Outputs/207Bi/processedSpectra/"+processedSpectraFolder+"NabSimulation_Output_CAL2702_207Bi_Sweep_"+to_string(Mylar)+"umMylar_"+to_string(approxRad)+".mmX_0.mmY_"+carrRad+"mmRad_1.umMaxStep_emlivermore_1.umCut_shift"+to_string(XDisp)+"mmX_"+to_string(YDisp)+"mmY_parsedHisto_0.root";
		pullSimHisto(simSpectra, simSpectraCenter);

		printf("%s\n", simSpectra.c_str());

		chiDummy = returnCombinedChi2("./parts/"+to_string(Mylar)+"umMylar"+carrRad+"mmCarrierRad/"+inputSpectrum+"_"+to_string(Mylar)+"umMylar_"+to_string(approxRad)+"mmXSimOffset_"+carrRad+"mmRad_"+to_string(XDisp)+"mmX_"+to_string(YDisp)+"mmY.txt");

		return 0;
	}

	int iStart = 0;
	int iEnd = 28;
	int loopIndex = 0;

	if(argc > 6 && anlysisMode == "Production") // With production runMode, passing an extra argument will trigger doing only half the grid, assuming double the number of nodes have been requested
	{
		int loopIndex = (Int_t)atof(argv[6]); // Need to start from 0

		iStart = loopIndex*4;
		iEnd = iStart+4;

		if(iEnd > 28) iEnd = 28;
	}

	printf("%d %d\n", iStart, iEnd);

	// Normal grid sweep otherwise
	for(int i=iStart;i<iEnd;i++)
	{
		// YDisp = -5.5+((double)(i))*0.5; // Y-displacement
		YDisp = -5.4+((double)(i))*0.4; // Y-displacement

		simSpectra = "/storage/ideas/is-ajezghani3-0/SourceSimulation_Outputs/207Bi/processedSpectra/"+processedSpectraFolder+"NabSimulation_Output_CAL2702_207Bi_Sweep_"+to_string(Mylar)+"umMylar_"+to_string(approxRad)+".mmX_0.mmY_"+carrRad+"mmRad_1.umMaxStep_emlivermore_1.umCut_shift"+to_string(XDisp)+"mmX_"+to_string(YDisp)+"mmY_parsedHisto_0.root";
		pullSimHisto(simSpectra, simSpectraCenter);

		printf("%s\n", simSpectra.c_str());

		chiDummy = returnCombinedChi2("./parts/"+to_string(Mylar)+"umMylar"+carrRad+"mmCarrierRad/"+inputSpectrum+"_"+to_string(Mylar)+"umMylar_"+to_string(approxRad)+"mmXSimOffset_"+carrRad+"mmRad_"+to_string(XDisp)+"mmX_"+to_string(YDisp)+"mmY.txt");
	}

	return 0;
}
