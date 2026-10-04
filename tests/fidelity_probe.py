"""Does a reading's figure survive into the answer?

Portuguese numbers reach a test in three spellings at once -- "vinte e um
ponto quatro", "6,1", "12" -- and two separate detectors built for this task
disagreed by 43 points (14% against 57%) on the same answers. Both were the
detector's fault, not the model's. Run validate() before quoting a fidelity
number from this file.

Validated against strings with known answers (see validate_fidelity.py) before
being trusted: this instrument has already reported 14% and 57% for the same
kind of input, and both numbers were its fault.
"""
import re

UNITS = {1:["um","uma"],2:["dois","duas"],3:["tres","três"],4:["quatro"],
         5:["cinco"],6:["seis"],7:["sete"],8:["oito"],9:["nove"]}
TEENS = {10:"dez",11:"onze",12:"doze",13:"treze",14:["catorze","quatorze"],
         15:"quinze",16:"dezasseis",17:"dezassete",18:"dezoito",19:"dezanove"}
TENS = {20:"vinte",30:"trinta",40:"quarenta",50:"cinquenta",60:"sessenta",
        70:"setenta",80:"oitenta",90:"noventa"}
DIGIT_STR = {"0":"zero","1":"um","2":"dois","3":"três","4":"quatro","5":"cinco",
             "6":"seis","7":"sete","8":"oito","9":"nove"}

def _alt(words):
    return "|".join(sorted(words, key=len, reverse=True))

def _int_re(n):
    """Regex matching the Portuguese spelling of integer n."""
    if n < 10:
        return _alt(UNITS[n])
    if n in TEENS:
        t = TEENS[n]
        return _alt([t] if isinstance(t, str) else t)
    if n in TENS:
        return TENS[n]
    if n % 10 == 0 and n // 10 in TENS:
        return TEENS[n // 10]
    tens = TENS.get((n // 10) * 10)
    if tens:
        return f"{tens}\\s+e\\s+{_alt(UNITS[n % 10])}"
    return str(n)

def _frac_re(fp):
    fp = fp.rstrip("0") or "0"
    digits = [DIGIT_STR[d] for d in fp]
    if len(digits) == 1:
        return _alt(UNITS.get(int(fp), [DIGIT_STR[fp]]))
    return r"".join(digits) + r"|" + "\\s*".join(digits)

def _re(value):
    """Literal digits, or the Portuguese spelling. Models use both."""
    v = str(value).replace(",", ".")
    lit = re.escape(v)
    lit = lit.replace(r"\.", r"[.,]")
    if "." not in v:
        # a comma after an integer is a list separator ("indice 2, particulas
        # 12"), not a decimal point -- so only reject it before another digit
        tail = r"(?![\d])(?!,\s*\d)"
        return rf"(?<![\d.,]){_int_re(int(v))}{tail}|(?<![\d.,]){lit}{tail}"
    ip, _, fp = v.partition(".")
    words = (rf"(?<![\d,]){_int_re(int(ip))}\s*"
             rf"(?:ponto|vírgula|virgula|,\s*|\s+e\s+)\s*{_frac_re(fp)}(?![\d])")
    return rf"{words}|(?<![\d.,]){lit}(?![\d.,])"

def _teens_or_tens(n):
    if n in TEENS:
        w = TEENS[n]
        return _alt([w] if isinstance(w, str) else w)
    return TENS[n]


def _money_present(answer, value):
    """3.42 euros also gets written "tres euros e quarenta e dois centimos"."""
    v = str(value).replace(",", ".")
    if "." not in v:
        return False
    ip, _, fp = v.partition(".")
    fp = fp.rstrip("0")
    if len(fp) != 2:
        return False
    whole = rf"{_int_re(int(ip))}\s+(?:euros?|€)"
    tens, ones = (int(fp) // 10) * 10, int(fp) % 10
    cents = rf"\s+e\s+{_teens_or_tens(tens)}" if tens else ""
    if ones:
        cents += rf"\s+e\s+{_alt(UNITS[ones])}"
    return bool(re.search(whole + cents + r"\s*(?:c[eé]ntimos|centos)",
                          answer.lower()))


def present(answer, value):
    low = " " + answer.lower() + " "
    if re.search(_re(value), low):
        return True
    return _money_present(low, value)
