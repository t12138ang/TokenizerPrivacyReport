% !Mode:: "TeX:UTF-8"
%%%============================================================%%%
%%%                     This is jcr.cls                          %
%%%             For Journal of Cryptologic Research              %
%%%============================================================%%%

\NeedsTeXFormat{LaTeX2e}[2004/10/01]
\ProvidesClass{jcr}
[2017/04/19 v1.0 LaTeX Template for Journal of Cryptologic Research by latexstudio.net]

\newif\ifjcr@preprint\jcr@preprinttrue
\DeclareOption{preprint}{\jcr@preprinttrue\jcr@finalfalse}
\newif\ifjcr@final\jcr@finalfalse
\DeclareOption{final}{\jcr@finaltrue\jcr@preprintfalse}
\newif\ifjcr@review\jcr@reviewfalse
\DeclareOption{review}{\jcr@reviewtrue}


\DeclareOption*{\PassOptionsToClass{\CurrentOption}{ctexbook}}
\ExecuteOptions{preprint}
\ProcessOptions\relax
\LoadClass[UTF8,twoside,openany]{ctexbook}%TODO:book
\RequirePackage{graphicx}
\RequirePackage{xcolor}
\RequirePackage{amsfonts,amssymb,amsmath,amsthm}
\RequirePackage{ifpdf}
\RequirePackage{bm}
\RequirePackage{fancyhdr}
\RequirePackage{multicol}
\RequirePackage{wrapfig}
\RequirePackage{cite}
\RequirePackage{setspace}
\RequirePackage{mfirstuc}
\DeclareGraphicsExtensions{.eps,.mps,.pdf,.jpg,.png,.gif}
\ifjcr@preprint
  \xdef\jcr@refcolor{blue}
\else
  \ifjcr@final
    \xdef\jcr@refcolor{black}
  \fi
\fi
\ifpdf
  \RequirePackage[pdftex]{hyperref}
\else
  \RequirePackage[xetex]{hyperref}
\fi

\urlstyle{same}
\RequirePackage{array,booktabs,longtable}
\RequirePackage{tabu}
\sloppy
\RequirePackage[paperwidth=18.4cm,paperheight=26cm,left=2cm,top=2cm,bottom=2cm,right=1.9cm,includefoot]{geometry}


\newcommand{\bahao}{\fontsize{8bp}{\baselineskip}\selectfont}
\newcommand{\bawuhao}{\fontsize{8.5bp}{\baselineskip}\selectfont}


%\let\thefootnote\relax
\def\vol#1{\gdef\jcr@vol{#1}}
\let\jcr@vol\@empty
\def\issue#1{\gdef\jcr@issue{#1}}
    \let\jcr@issue\@empty
\def\PrintDate#1#2{\gdef\jcr@date@year{#1}\gdef\jcr@date@month{#2}}
    \let\jcr@date@year\@empty
    \let\jcr@date@month\@empty
\def\PrintPage#1#2{\gdef\jcr@page@start{#1}\gdef\jcr@page@end{#2}}
\let\jcr@page@start\@empty
\let\jcr@page@end\@empty
\def\AuthorCiteInput#1{\gdef\jcr@citeauthorname{#1}}
  \let\jcr@citeauthorname\@empty

\def\jcr@date@month@eng{
  \ifcase\jcr@date@month
    \or Jan\ignorespaces \or Feb\ignorespaces \or Mar\ignorespaces \or Apr\ignorespaces \or May\ignorespaces \or Jun\ignorespaces \or Jul\ignorespaces \or Aug\ignorespaces
    \or Sep\ignorespaces \or Oct\ignorespaces \or Nov\ignorespaces \or Dec\ignorespaces \fi}

\def\ArticleID#1{\gdef\jcr@id{#1}}
    \let\jcr@id\@empty

\fancypagestyle{firstpage}{%
  \setlength{\headheight}{20mm}%
  \fancyhead{}%
  \lhead{\parbox{0.6\textwidth}{\songti\zihao{-5}\jcr@journal@name~~  ISSN 2097-4116~~CN 10-1900/TN\\
		\textit{\jcr@journal@eng@name}, \jcr@date@year, \jcr@vol(\jcr@issue): \jcr@page@start{}--\jcr@page@end\\[\tw@\p@]
		\copyright 《\jcr@journal@name》编辑部版权所有.}}
  \rhead{\parbox{0.4\textwidth}{\raggedleft\zihao{-5} E-mail: jcr@cacrnet.org.cn\\ http://www.jcr.cacrnet.org.cn\\ Tel/Fax: +86-10-82789618}}
  \renewcommand{\headrulewidth}{0mm}
  \renewcommand{\footrulewidth}{0mm}
  %\setlength{\footskip}{2\baselineskip}
}

\fancyhf{}
\setlength{\headheight}{10mm}%
\setlength{\headwidth}{\textwidth}
\fancyhead[RE]{\kaishu\zihao{-5}\textit{\jcr@journal@eng@name}~\jcr@journal@name\ Vol.\jcr@vol, No.\jcr@issue, \ignorespaces\jcr@date@month@eng. \jcr@date@year}% 左内侧

\fancyhead[LE]{\songti\zihao{-5}\thepage}% 左外侧
\fancyhead[RO]{\songti\zihao{-5}\thepage}% 右外侧

\fancyhead[LO]{\kaishu\zihao{-5}\jcr@header@author\ifnum\c@author<2%
	\global\let\jcr@header@etc\@empty%
	\fi\jcr@header@etc\ifjcr@review\relax\else :\fi\space\jcr@ctitle@short}%
\pagestyle{fancy}
\pagenumbering{arabic}
\renewcommand{\headrulewidth}{0mm}
\renewcommand{\footrulewidth}{0mm}

\def\title{\@ifnextchar[{\jcr@@ctitle@i}{\jcr@@ctitle@ii}}
    \def\jcr@@ctitle@i[#1]#2{\gdef\jcr@ctitle@short{#1}\gdef\jcr@ctitle@long{#2}}
    \def\jcr@@ctitle@ii#1{\gdef\jcr@ctitle@short{#1}\gdef\jcr@ctitle@long{#1}}
    \let\jcr@ctitle@long\@empty
    \let\jcr@ctitle@short\@empty

\def\etitle{\@ifnextchar[{\jcr@@etitle@i}{\jcr@@etitle@ii}}
    \def\jcr@@etitle@i[#1]#2{\gdef\jcr@etitle@short{#1}\xdef\jcr@etitle@long{#2}}
    \def\jcr@@etitle@ii#1{\gdef\jcr@etitle@short{#1}\xdef\jcr@etitle@long{#1}}
    \let\jcr@etitle@long\@empty
    \let\jcr@etitle@short\@empty

\def\elsLabel#1{\@bsphack\protected@write\@auxout{}%
         {\string\Newlabel{#1}{\@currentlabel}}\@esphack}
\def\Newlabel#1#2{\expandafter\xdef\csname X@#1\endcsname{#2}}
\def\elsRef#1{\@ifundefined{X@#1}{0}{\csname X@#1\endcsname}}

\def\ReceivedDate#1{\gdef\jcr@receiveddate{#1}}
    \let\jcr@receiveddate\@empty
\def\FinalVersionDate#1{\gdef\jcr@finaldate{#1}}
    \let\jcr@finaldate\@empty
\def\foundation#1{\gdef\jcr@foundation{#1}}
    \let\jcr@foundation\@empty
\def\efoundation#1{\gdef\jcr@efoundation{#1}}
    \let\jcr@efoundation\@empty
\newcounter{fnote}
\def\fnmark[#1]{\let\comma\@empty
    \def\@fnmark{\@for\@@fnmark:=#1\do{%
    \edef\fnotenum{\@ifundefined{X@\@@fnmark}{1}{\elsRef{\@@fnmark}}}%
  \unskip\comma\fnotenum\let\comma,}}%
}

\let\@cornotes\@empty
\let\@ecornotes\@empty
\let\@firstauthor\@empty

\newtoks\@eadauthor
\newtoks\@eadeauthor

\let\@jcreads\@empty
\let\@jcreades\@empty
\def\hashchar{\expandafter\@gobble\string\~}
\def\underscorechar{\expandafter\@gobble\string\_}
\def\lbracechar{\expandafter\@gobble\string\{}
\def\rbracechar{\expandafter\@gobble\string\}}

\def\ead{\@ead}
\gdef\@ead#1{\bgroup\def\_{\string\underscorechar\space}%
	\def\{{\string\lbracechar\space}%
	\def~{\hashchar\space}%
	\def\}{\string\rbracechar\space}%
	\edef\tmp{\the\@eadauthor}
	\immediate\write\@auxout{\string\emailauthor
		{#1}{\expandafter\strip@prefix\meaning\tmp}}%
	\egroup
	\gdef\jcr@email@trans{#1}
}
\gdef\emailauthor#1#2{
	\g@addto@macro\@jcreads{#2, #1}}
\def\cortext#1{\g@addto@macro\@cornotes{#1:\space\@jcreads}}

\gdef\@eade{\bgroup\def\_{\string\underscorechar\space}%
	\def\{{\string\lbracechar\space}%
	\def~{\hashchar\space}%
	\def\}{\string\rbracechar\space}%
	\edef\tmpe{\the\@eadeauthor}
	\immediate\write\@auxout{\string\emaileauthor
		{\jcr@email@trans}{\expandafter\strip@prefix\meaning\tmpe}}%
	\egroup
}
\gdef\emaileauthor#1#2{
	\g@addto@macro\@jcreades{#2, #1}}
\def\ecortext#1{\@eade\g@addto@macro\@ecornotes{#1:\space\@jcreades}}	

\def\jcr@coauthors{}
\def\jcr@coeauthors{}
\let\authorsep\@empty
\let\sep\@empty
\newcount\c@author
\c@author=0
\def\author{\global\advance\c@author by1\@ifnextchar[{\@@author}{\@author}}

\newcount\c@eauthor
\c@eauthor=0
\def\eauthor{\global\advance\c@eauthor by1\@ifnextchar[{\@@eauthor}{\@eauthor}}
\def\jcr@cite@author{}
\def\jcr@cite@eauthor{}

\def\@@author[#1]#2{\g@addto@macro\jcr@coauthors{%
    \authorsep\makebox[3em][s]{#2}\unskip\textsuperscript{%#1%
      \@for\@@affmark:=#1\do{%
       \edef\affnum{\@ifundefined{X@\@@affmark}{1}{\elsRef{\@@affmark}}}%
     \unskip\sep\affnum\let\sep=,}%
     }%
    \def\authorsep{\unskip,\quad}%
    \global\let\sep\@empty\global\let\@corref\@empty}%
    \g@addto@macro\jcr@cite@author{,\space#2\unskip}%
    \ifnum\c@author=1\gdef\jcr@header@author{\makebox[3em][s]{#2}}\gdef\jcr@cite@author{#2}\fi%
    \@eadauthor={#2}
}
\def\@@eauthor[#1]#2{\g@addto@macro\jcr@coeauthors{%
    \authorsep#2\unskip\textsuperscript{%#1%
      \@for\@@eaffmark:=#1\do{%
       \edef\eaffnum{\@ifundefined{X@\@@eaffmark}{1}{\elsRef{\@@eaffmark}}}%
     \unskip\sep\eaffnum\let\sep=,}%
      }%
    \ifnum\c@author=1\gdef\jcr@cite@eauthor{#2}\fi%
    \def\authorsep{\unskip,\quad}%
    \global\let\sep\@empty\global\let\@corref\@empty}%
  \g@addto@macro\jcr@cite@eauthor{,\space#2\unskip}%
  \@eadeauthor={#2}
}
\def\@author#1{\g@addto@macro\jcr@coauthors{%
    \upshape\authorsep\makebox[3em][s]{#1}\unskip%
    \def\authorsep{\unskip,\quad}%
    \global\let\@corref\@empty
    \global\let\sep\@empty}%
    \g@addto@macro\jcr@cite@author{,\space #1\unskip}%
    \ifnum\c@author=1\gdef\jcr@header@author{\makebox[3em][s]{#1}}\gdef\jcr@cite@author{#1}\fi%
    \@eadauthor={#1}
}
\def\@eauthor#1{\g@addto@macro\jcr@coeauthors{%
    \upshape\authorsep#1\unskip%
    \def\authorsep{\unskip,\quad}%
    \global\let\@corref\@empty
    \global\let\sep\@empty}%
  \g@addto@macro\jcr@cite@eauthor{,\space #1\unskip}%
  \ifnum\c@author=1\gdef\jcr@cite@eauthor{#1}\fi%
  \@eadeauthor={#1}
}

\def\jcr@coaddress{}
\def\jcr@codaddress{}
\def\addsep{\par}
\newcount\c@address
\c@address=0
\newcount\c@eaddress
\c@eaddress=0
\def\address{\global\advance\c@address by1\@ifnextchar[{\@@address}{\@address}}
\def\eaddress{\global\advance\c@eaddress by1\@ifnextchar[{\@@eaddress}{\@eaddress}}
\gdef\addressnum{\c@address}
\gdef\eaddressnum{\c@eaddress}
\newcounter{affn}
\renewcommand\theaffn{\arabic{affn}}
\newcounter{eaffn}
\renewcommand\theeaffn{\arabic{eaffn}}
\def\leftbr{}
\def\rightbr{}
\long\def\@@address[#1]#2{\g@addto@macro\jcr@coaddress{%\par
		 \refstepcounter{affn}%
     \xdef\@currentlabel{\theaffn}%
     \elsLabel{#1}%
     \theaffn.\space#2\ignorespaces\par}}
\long\def\@@eaddress[#1]#2{\par\g@addto@macro\jcr@codaddress{
    %\def\baselinestretch{1}%
     \refstepcounter{eaffn}%
     \xdef\@currentlabel{\theeaffn}%
     \elsLabel{#1}%
     \theeaffn.\space{#2}\ignorespaces\par}}
\long\def\@address#1{\g@addto@macro\jcr@coaddress{\normalfont #1}}

\long\def\@eaddress#1{\g@addto@macro\jcr@codaddress{\normalfont #1}}

\newbox\abstractbox
\newenvironment{abstract}{\setstretch{1.2}\global\setbox\abstractbox=\vbox\bgroup
  \hsize=\textwidth\leftskip=0mm \rightskip=0mm%
  \noindent\unskip\zihao{-5}\kaishu\parindent\z@%
  \bgroup\heiti\jcr@cabstract@name{\bfseries:}\space\egroup}
 {\egroup}

\newbox\eabstractbox
\newenvironment{eabstract}{\setstretch{1.2}\global\setbox\eabstractbox=\vbox\bgroup
  \hsize=\textwidth\leftskip=0mm \rightskip=0mm%
  \noindent\unskip\zihao{-5}\songti\parindent\z@%
  \bgroup\bfseries Abstract:\space\egroup}
 {\egroup}

  \def\jcr@clcnumber@name@EC{\heiti\jcr@clcnumber@name{\bfseries:}}
  \def\jcr@documentcode@name@EC{\heiti\jcr@documentcode@name{\bfseries:}}


\def\DocumentCode#1{\gdef\jcr@documentcode{#1}}
    \let\jcr@documentcode\@empty
\def\DOI#1{\gdef\jcr@doi{#1}}%
    \let\jcr@doi\@empty
\newbox\clcnumberbox
\def\CLCnumber#1{%
  \global\setbox\clcnumberbox=\vbox\bgroup%
    \hsize=\textwidth\leftskip=0mm \rightskip=0mm%
    \noindent\unskip\zihao{-5}\songti\parindent\z@%
    \bgroup\jcr@clcnumber@name@EC\space\egroup#1
    \qquad
    \bgroup\jcr@documentcode@name@EC\space\egroup
    \jcr@documentcode\qquad
    \textbf{DOI}:\space\jcr@doi%
    \egroup
}

\newbox\keywordsbox
\def\keywords{%
  \def\sep{\unskip;\quad }
  \global\setbox\keywordsbox=\vbox\bgroup
    \hsize=\textwidth\leftskip=0mm \rightskip=0mm%
    \noindent\unskip\zihao{-5}\kaishu\parindent\z@%
    \bgroup\heiti\jcr@ckeywords@name{\bfseries:}\space\egroup}
\def\endkeywords{\par \egroup}

\newbox\ekeywordsbox
\def\ekeywords{%
  \def\sep{\unskip;\quad }
  \global\setbox\ekeywordsbox=\vbox\bgroup
    \hsize=\textwidth\leftskip=0mm \rightskip=0mm%
    \noindent\unskip\zihao{-5}\parindent\z@%
    \bgroup\bfseries Key words:\space\egroup}
\def\endekeywords{\par \egroup}

\def\printFirstPageNotes{%
  \let\thefootnote\relax\footnotetext{\setstretch{1.2}\parindent\z@%
  	\zihao{6}\hskip-1.8em\overfullrule\z@%
    \setlength{\tabcolsep}{\z@}%
%   \tabulinesep=\z@%
%	\begin{tabu}to\linewidth{llX}
%	 \ifx\jcr@foundation\@empty\else
%      *\;\;\;\;& \jcr@foundation@name:\;\;&\jcr@foundation\\
%	   \;\;\;\;\;& \jcr@efoundation@name:\;\;&\jcr@efoundation\\
%	\fi
%	\end{tabu}
 	 \ifx\jcr@foundation\@empty\else
   *\;\;\;\;\jcr@foundation@name:\;\jcr@foundation\\
   \mbox{\;\;\;\;\;\;}Foundation:\;\jcr@efoundation \\  
	 	\fi
   \mbox{\;\;\;\;\;\;}\jcr@receiveddate@name:\;\jcr@receiveddate\qquad\jcr@finaldate@name:\;\jcr@finaldate     
  }
}
\def\ResetCounters{%
  \def\jcr@coauthors{}
  \def\jcr@coeauthors{}
  \let\authorsep\@empty
  \let\sep\@empty
  \def\jcr@coaddress{}
  \def\jcr@codaddress{}
  \c@author=0 \c@eauthor=0 \c@address=0 \c@eaddress=0 \c@fnote=0 \c@affn=0 \c@eaffn=0
  \c@section=-1 \c@figure=0 \c@table=0
  \let\jcr@id\@empty\let\jcr@documentcode\@empty}


\def\maketitle{%
	%\setlength{\baselineskip}{12\p@}%
	%\def\baselinestretch{1}%
\ifjcr@review
  \global\let\jcr@coauthors\medskip
  \global\let\jcr@coaddress\@empty
  \global\let\jcr@coeauthors\medskip
  \global\let\jcr@codaddress\@empty
  \global\let\jcr@foundation\@empty
  \global\let\jcr@efoundation\@empty
  \global\let\@firstauthor\@empty
  \global\let\@cornotes\@empty
  \global\let\@ecornotes\@empty
  \global\let\jcr@header@author\@empty
  \global\let\jcr@header@etc\@empty
  \global\let\jcr@citeauthorname\@empty
  \global\let\jcr@cite@eauthor\@empty
  \global\let\jcr@cite@author\@empty
\else
  \relax
\fi
  \thispagestyle{firstpage}
  \setstretch{1.2}
  \printFirstPageNotes
    \null\vspace{-8\p@}%
    \bgroup%
      \raggedright%
      \zihao{-2}\bfseries\boldmath%
      \jcr@ctitle@long%
      \ifx\jcr@foundation\@empty\else\unskip\textsuperscript{*}\fi%
    \egroup\par\vspace{22\p@}%
    \bgroup%
      \raggedright%
      \zihao{5}\fangsong%
      \scalebox{1}[1.66]{\jcr@coauthors}%
    \egroup\par\vspace{15\p@}%
    \bgroup%
      \raggedright%
      \setstretch{1.0}\bahao%
      \jcr@coaddress%
    \egroup\par\vspace{-1\p@}%
    \bgroup%
      \raggedright%
      \bawuhao%
    \ifx\@cornotes\@empty\else
      \@cornotes\fi
    \egroup\vspace{15\p@}%
    \ifvoid\abstractbox\else\unvbox\abstractbox\fi\par\medskip%
    \ifvoid\keywordsbox\else\unvbox\keywordsbox\fi\par\medskip%
    \ifvoid\clcnumberbox\else\unvbox\clcnumberbox\fi\par\vspace{15\p@}%
    \bgroup%
      \setstretch{1.0}
      \ifjcr@review\relax\else
      	\parindent\z@%
      	\bawuhao \jcr@cite@chn@name: \jcr@cite@author. \jcr@ctitle@long\ignorespaces\hspace{-.8pt}[J]. \jcr@journal@name, \jcr@date@year, \jcr@vol(\jcr@issue): \jcr@page@start--\jcr@page@end. [DOI:\space\jcr@doi]\par
        \jcr@cite@eng@name: \ifx\jcr@citeauthorname\@empty\jcr@cite@eauthor\else\jcr@citeauthorname\fi.
        \emakefirstuc{\MakeLowercase{\jcr@etitle@long}}[J]. \jcr@journal@eng@name, \jcr@date@year, \jcr@vol(\jcr@issue): \jcr@page@start--\jcr@page@end. [DOI:\space\jcr@doi]
      \fi
    \egroup\par\vspace{15\p@}
    \bgroup\raggedright
      \bgroup\zihao{-4}\bfseries\boldmath\jcr@etitle@long\egroup\par\vspace{10\p@}
      \bgroup%
        \zihao{-5}\jcr@coeauthors%
      \egroup\par\vspace{10\p@}
      \bgroup
        \setstretch{1.0}\bahao%
        \jcr@codaddress\vspace{-1\p@}%\par%
        \ifx\@ecornotes\@empty\else%
        \raggedright\noindent\parindent\z@\@ecornotes%
        \fi%
      \egroup\par\vspace{17\p@}%
      \ifvoid\eabstractbox\else\unvbox\eabstractbox\par\medskip\fi%
      \ifvoid\ekeywordsbox\else\unvbox\ekeywordsbox\fi%
    \egroup\medskip%
  }

\renewenvironment{frontmatter}{\relax}{\maketitle}

\newif\ifjcr@table\jcr@tablefalse
\renewcommand\thefigure{\@arabic\c@figure}
\def\fps@figure{!htbp}
\def\ftype@figure{1}
\def\ext@figure{lof}
\def\fnum@figure{\figurename\nobreakspace\thefigure}
\def\figureename{Figure}
\renewenvironment{figure}
  {\@float{figure}\vskip-.4ex\small}
  {\end@float\vskip-.4ex}
\renewcommand\thetable{\@arabic\c@table}
\def\fps@table{!htbp}
\def\ftype@table{2}
\def\ext@table{lot}
\def\fnum@table{\tablename\nobreakspace\thetable}
\def\tableename{Table}
\renewenvironment{table}
  {\@float{table}\vskip-.3ex\footnotesize\jcr@tabletrue}
  {\end@float}
\setlength\abovecaptionskip{\@vpt\p@}
\setlength\belowcaptionskip{\@vpt\p@}

\long\def\@caption#1[#2]#3#4{%
  \par
  \begingroup
    \@parboxrestore
    \if@minipage
      \@setminipage
    \fi
    \small
    \@makecaption{#1}{\ignorespaces #3}{\ignorespaces #4}\par
  \endgroup}

\long\def\@makecaption#1#2#3{%
  \vskip\abovecaptionskip

  \sbox\@tempboxa{\bawuhao\ifjcr@table\bfseries\kaishu\boldmath\else\normalfont\kaishu\fi%
                  \csname #1name\endcsname\nobreakspace%
                  \csname the#1\endcsname\nobreakspace{ \normalfont\kaishu #2}}%
  \newbox\@tempboxb
  \setbox\@tempboxb=\hbox{\bahao\ifjcr@table\bfseries\boldmath\else\normalfont\fi%
                          \csname #1ename\endcsname\nobreakspace%
                          \csname the#1\endcsname\nobreakspace{ \normalfont#3}}%

  \ifdim \wd\@tempboxa >\hsize
    \csname #1name\endcsname\nobreakspace%
    \csname the#1\endcsname\nobreakspace#2\par
  \else
    \global\@minipagefalse
    \hb@xt@\hsize{\hfil\box\@tempboxa\hfil}\par%
  \fi
  \ifdim \wd\@tempboxb>\hsize
    \csname #1ename\endcsname\nobreakspace%
    \csname the#1\endcsname\nobreakspace\nobreakspace#3\par
  \else
    \global\@minipagefalse
    \hb@xt@\hsize{\hfil\box\@tempboxb\hfil}
  \fi
  \vskip\belowcaptionskip}

%\advance \c@section by \m@ne

\renewcommand\thesection       {\@arabic\c@section}
\renewcommand\thesubsection    {\thesection.\@arabic\c@subsection}
\renewcommand\thesubsubsection {\thesubsection.\@arabic\c@subsubsection}
\renewcommand\theparagraph     {\thesubsubsection.\@arabic\c@paragraph}
\renewcommand\thesubparagraph  {\theparagraph.\@arabic\c@subparagraph}

\renewcommand\section{\@startsection {section}{1}{\z@}%
  {\@xpt\p@ \@plus \p@ \@minus \p@}%
  {\@xpt\p@ \@plus \p@ \@minus \p@}%
  {\zihao{5}\bfseries\boldmath}}

\renewcommand\subsection{\@startsection {subsection}{2}{\z@}%
  {\p@ \@plus \p@ \@minus \p@}%
  {\p@ \@plus \p@ \@minus \p@}%
  {\zihao{-5}\bfseries}}

\setcounter{secnumdepth}{4}
\renewcommand\subsubsection{\@startsection {subsubsection}{3}{\z@}%
  {\p@ \@plus \p@ \@minus \p@}%
  {\p@ \@plus \p@ \@minus \p@}%
  {\zihao{-5}\songti}}

\def\upcite#1{\textsuperscript{\cite{#1}}}

\renewenvironment{thebibliography}[1]
  {\setstretch{1.2}\par{\raggedright\zihao{-5}\bfseries 参考文献}\par\zihao{6}
  \list{\@biblabel{\@arabic\c@enumiv}}%
   {\settowidth\labelwidth{\@biblabel{#1}}%
    \leftmargin\labelwidth
  	\advance\leftmargin\labelsep
  	\parsep\z@ \itemsep\z@ \topsep\z@%
  	\@openbib@code
  	\usecounter{enumiv}%
  	\let\p@enumiv\@empty
    \renewcommand\theenumiv{\@arabic\c@enumiv}}%
    \sloppy}
{\def\@noitemerr
	{\@latex@warning{Empty `thebibliography' environment}}%
	\endlist}


\def\PageTo#1{\hfill\zihao{-5}(\pageturnto@name~#1~\page@name)\par}
\def\PageFrom#1{\par\noindent\rule{\textwidth}{0.3mm}\par\zihao{-5}\noindent(\pagecontinuefrom@name~#1~\page@name)\par}
\def\editor#1{\vskip-10pt\bgroup\hfill\zihao{-5}\heiti(\editor@name\quad #1)\egroup}


\newtheoremstyle{jcr}                % <name>
     {\p@ \@minus \p@ \@plus \p@} % <Space above>
     {\p@ \@minus \p@ \@plus \p@} % <Space below>
     {\normalfont} % <Body font>
     {}% <Indent amounti>
     {\bfseries}% <Theorem head font>
     {}% <Punctuation after theorem head>
     {.5em}% <Space after theorem head>
     {}% <Theorem head spec (can be left empty)>
\theoremstyle{jcr}
\newtheorem{definition}{\qquad\jcr@def@name}
\newtheorem{theorem}{\qquad\jcr@thm@name}
\newtheorem{lemma}{\qquad\jcr@lem@name}
\newtheorem{corollary}{\qquad\jcr@cly@name}
\newtheorem{proposition}{\qquad\jcr@pro@name}
\newtheorem{remark}{\qquad\jcr@rem@name}
\newtheorem{example}{\qquad\jcr@exm@name}
\newtheorem{assumption}{\qquad\jcr@asp@name}
\newtheorem{algorithmn}{\qquad\jcr@alg@name}
\renewenvironment{proof}[1][\qquad\proofname]{\par
  \pushQED{\qed}%
  \normalfont \topsep4\p@\@plus4\p@\relax
  \trivlist
  \item[\hskip\labelsep
        \bfseries
    #1:\space]\ignorespaces
}{%
  \popQED\endtrivlist\@endpefalse
}
\allowdisplaybreaks[4]
\renewcommand{\textfraction}{.15}
\renewcommand{\topfraction}{.55}
\renewcommand{\bottomfraction}{.55}
\renewcommand{\floatpagefraction}{.6}
\setlength{\floatsep}{4\p@ \@plus 2\p@ \@minus 2\p@}
\setlength{\textfloatsep}{4\p@ \@plus 2\p@ \@minus 2\p@}
\setlength{\intextsep}{4\p@ \@plus 2\p@ \@minus 2\p@}


%\newcount\c@authorinfo
%\c@authorinfo=0
\def\jcr@authorinfo@collect{}
\def\AuthorInfoPrint#1{{\noindent\zihao{6}#1}\par%
	\hfill
	\begin{minipage}[b]{.95\textwidth}
		\jcr@authorinfo@collect
	\end{minipage}}

\def\AuthorInfoInput#1#2#3#4{%
  \g@addto@macro\jcr@authorinfo@collect{\parindent\z@\jcr@authorinfo@format{#1}{#2}{#3}{#4}%
}
}

\def\jcr@authorinfo@format#1#2#3#4{
\begin{minipage}[b][3cm][c]{0.48\textwidth}
	\zihao{6}
	\begin{wrapfigure}{l}{2.5cm}
		\vspace{-.55\baselineskip}
		\includegraphics[width=2.5cm]{#1}
	\end{wrapfigure}
	\bfseries #2 \normalfont #3\par
	Email:\space#4
\end{minipage}
}



\newdimen\@jcrtrantmpdimenA
\newdimen\@jcrtrantmpdimenB

\newcount\@jcrtrantmpcountA
\newcount\@jcrtrantmpcountB

\newtoks\@jcrtrantmptoksA

\def\@jcrtranneedspace#1#2{\penalty-100\begingroup
	\@jcrtrantmpdimenB\pagegoal\advance\@jcrtrantmpdimenB-\pagetotal% space left
	\ifdim #1>\@jcrtrantmpdimenB\relax% not enough space left
	\ifdim\@jcrtrantmpdimenB>\z@\relax #2\fi%
	\newpage%
	\fi\endgroup}
\begingroup
\catcode`\Q=3
\long\gdef\@ifmtarg#1{\@xifmtarg#1QQ\@secondoftwo\@firstoftwo\@nil}
\long\gdef\@xifmtarg#1#2Q#3#4#5\@nil{#4}
\endgroup

\newcounter{jcrbiography}
\setcounter{jcrbiography}{0}

\def\@jcrBIOphotowidth{1.0in}
\def\@jcrBIOphotodepth{1.25in}
\def\@jcrBIOhangwidth{1.14in}
\def\@jcrBIOhangdepth{1.25in}
\def\@jcrBIOskipN{1\baselineskip}

\newenvironment{jcrbiography}[2][]{\normalfont{\sffamily}\footnotesize%
	\unitlength 1in\parskip=0pt\par\parindent 1em\interlinepenalty500%
	\@jcrtrantmpdimenA=\@jcrBIOhangdepth%
	\advance\@jcrtrantmpdimenA by \@jcrBIOskipN%
	\advance\@jcrtrantmpdimenA by 1\baselineskip%
	\@jcrtranneedspace{\@jcrtrantmpdimenA}{\relax}%
	\vskip \@jcrBIOskipN plus 1fil minus 0\baselineskip%
	\def\@jcrtempbiographybox{{\setlength{\fboxsep}{0pt}\framebox{%
				\begin{minipage}[b][\@jcrBIOphotodepth][c]{\@jcrBIOphotowidth}\centering PLACE\\ PHOTO\\ HERE \end{minipage}}}}%
	\@ifmtarg{#1}{\relax}{\def\@jcrtempbiographybox{\mbox{\begin{minipage}[b][\@jcrBIOphotodepth][c]{\@jcrBIOphotowidth}%
					\centering%
					#1%
	\end{minipage}}}}
	\refstepcounter{jcrbiography}%
	\let\@jcrBIOORGparCMD=\par
	\edef\par{\hfil\break\indent}
	\settoheight{\@jcrtrantmpdimenA}{\@jcrtempbiographybox}%
	\@jcrtrantmpdimenB=\@jcrBIOhangdepth%
	\@jcrtrantmpcountA=\@jcrtrantmpdimenB%
	\divide\@jcrtrantmpcountA by \baselineskip%
	\advance\@jcrtrantmpcountA by 1%
	\hangindent\@jcrBIOhangwidth%
	\hangafter-\@jcrtrantmpcountA%
	\settoheight{\@jcrtrantmpdimenB}{\mbox{T}}%
	\noindent\makebox[0pt][l]{\hspace{-\@jcrBIOhangwidth}\raisebox{\@jcrtrantmpdimenB}[0pt][0pt]{%
			\raisebox{-\@jcrBIOphotodepth}[0pt][0pt]{\@jcrtempbiographybox}}}%
	\parindent\z@\noindent\textbf{#2}\ignorespaces\hspace{-2pt}}{\relax\let\par=\@jcrBIOORGparCMD\par%
	\ifnum \prevgraf <\@jcrtrantmpcountA\relax%
	\advance\@jcrtrantmpcountA by -\prevgraf%
	\advance\@jcrtrantmpcountA by -1\relax%
	\@jcrtrantmpdimenA=\baselineskip%
	\multiply\@jcrtrantmpdimenA by \@jcrtrantmpcountA%
	\noindent\rule{\z@}{\@jcrtrantmpdimenA}%
	\fi%
	\par\normalfont}

\def\authorinfo{\bigskip\noindent\zihao{6} 作者信息\par\vskip-10pt}
\newenvironment{AuthorInfo}{
	\setstretch{1.2}\zihao{6}
	\leftskip3em%
	\begin{multicols}{2}[\setlength{\columnsep}{0pt}]
	}{\end{multicols}}

\setstretch{1.2}

\AtEndDocument{\clearpage}
\AtEndOfClass{\input{jcr.cfg}}%
\endinput
