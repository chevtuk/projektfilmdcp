# --- Imports ---
from flask import Flask, json, request, render_template, url_for
import requests
from bs4 import BeautifulSoup
import unicodedata
import re
from fuzzywuzzy import fuzz
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
import time # Importera time för sleep
from urllib.parse import quote_plus # För att URL-koda IMDb-sökning

logging.basicConfig(level=logging.DEBUG)

app = Flask(__name__)
# app.config['TEMPLATES_AUTO_RELOAD'] = True

# --- Konstanter ---
SFDB_BASE_URL = "https://www.svenskfilmdatabas.se"
SEARCH_URL_HTML = f"{SFDB_BASE_URL}/sv/"
IMDB_SEARCH_URL = "https://www.imdb.com/find/" # Bas-URL för IMDb-sökning

# --- Hjälpfunktioner (normalize_title, extract_itemid_from_url, get_sfdb_original_title oförändrade) ---

def normalize_title(title):
    """Normalisera titlar."""
    if not title: return ""
    try:
        title_no_year = re.sub(r'\s*\(\d{4}\)$', '', title).strip()
        title_no_year = title_no_year.replace(':', '').replace('[', '').replace(']', '')
        title = unicodedata.normalize('NFKD', title_no_year).encode('ascii', 'ignore').decode('utf-8')
        title = re.sub(r'[^a-zA-Z0-9\s]', '', title)
        return title.lower().strip()
    except Exception as e:
        logging.warning(f"Kunde inte normalisera titel: {title} - Fel: {e}")
        try:
            return re.sub(r'[^a-zA-Z0-9\s]', '', title).lower().strip()
        except:
             return title.lower()

def extract_itemid_from_url(url):
    """Extraherar itemid från en SFDb URL."""
    if not url: return None
    match = re.search(r'itemid=(\d+)', url)
    if match:
        return match.group(1)
    match = re.search(r'-(\d+)/?$', url)
    if match:
        return match.group(1)
    return None

def get_sfdb_original_title(movie_page_url):
    """Hämtar originaltitel från en individuell SFDb filmsida."""
    original_title = None
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(movie_page_url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        titles_heading = soup.find('h2', id='titles')
        if titles_heading:
            foldout_div = titles_heading.find_next_sibling('div', class_='accordion__foldout')
            if foldout_div:
                 info_table = foldout_div.select_one('table.information-table')
                 if info_table:
                     rows = info_table.find_all('tr')
                     for row in rows:
                         th = row.find('th')
                         td = row.find('td')
                         if th and td and 'originaltitel' in th.get_text(strip=True).lower():
                             li = td.find('li')
                             original_title_text = li.get_text(strip=True) if li else td.get_text(strip=True)
                             if '(' in original_title_text and ')' in original_title_text:
                                 ot_simple = re.sub(r'\s*\(.*\)$', '', original_title_text).strip()
                                 if ot_simple: original_title = ot_simple
                                 else: original_title = original_title_text
                             else: original_title = original_title_text
                             logging.debug(f"  > SFDb Originaltitel hittad: {original_title}")
                             break
        if not original_title:
            logging.debug(f"  > Ingen SFDb originaltitel hittad i tabellen för {movie_page_url}")
    except requests.exceptions.RequestException as e:
        logging.error(f"Nätverksfel vid hämtning av SFDb originaltitel från {movie_page_url}: {e}")
    except Exception as e:
        logging.error(f"Fel vid hämtning/parsing av SFDb originaltitel från {movie_page_url}: {e}")
    return original_title

# --- NY FUNKTION: Hämta poster från IMDb via skrapning ---
def get_imdb_poster(search_title, year=None, poster_size_param=""): # poster_size_param ignoreras ofta av IMDb nu
    """
    SÖKER på IMDb och skrapar bästa träffens sida för att hitta en poster URL.
    Returnerar URL (sträng) eller None. VARNING: Mycket bräcklig!
    """
    if not search_title:
        logging.warning("Tom söktitel skickades till get_imdb_poster.")
        return None

    normalized_search_title = normalize_title(search_title)
    logging.debug(f"  > Söker på IMDb: Titel='{search_title}' ({normalized_search_title}), År={year}")
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)', 'Accept-Language': 'en-US,en;q=0.9'} # Be om engelska

    # --- Steg 1: Sök på IMDb ---
    imdb_movie_url = None
    try:
        # q=söktitel, s=tt (sök bara titlar), ref_=fn_al_tt_1 (vanlig referer)
        search_query = f"{search_title} {year}" if year else search_title
        # Använd quote_plus för att koda söktermen korrekt för URL:en
        imdb_search_params = {'q': search_query, 's': 'tt', 'ref_': 'fn_al_tt_1'}
        
        logging.debug(f"  > IMDb Sök URL: {IMDB_SEARCH_URL} med params: {imdb_search_params}")
        response = requests.get(IMDB_SEARCH_URL, params=imdb_search_params, headers=headers, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        # Hitta bästa träffen (detta är en GISSNING på IMDb:s struktur)
        # Försök med den modernare IPC-strukturen först
        best_match_score = -1
        best_match_link = None

        # Anpassa selectorn baserat på aktuell IMDb-struktur!
        # Tidigare: '.findResult', '.result_text'
        # Nyare: '.ipc-metadata-list-summary-item' eller liknande
        possible_results = soup.select('main section[data-testid="find-results-section-title"] ul li div') # Gissning!

        if not possible_results: # Fallback till äldre struktur?
             possible_results = soup.select('.findResult')
             logging.debug("  > IMDb: Använder fallback-selector '.findResult'.")


        logging.debug(f"  > IMDb Sökning hittade {len(possible_results)} potentiella element.")

        for result in possible_results:
             # Extrahera titel, år och länk från resultatet (anpassa selectors!)
             title_tag = result.select_one('a[class*="ipc-metadata-list-summary-item__t"], .result_text a') # Gissning
             year_tag = result.select_one('span[class*="ipc-metadata-list-summary-item__li"], .result_text') # Gissning - år kan vara i texten

             if not title_tag: continue

             result_title_text = title_tag.get_text(strip=True)
             result_url = title_tag.get('href')

             if not result_title_text or not result_url or not result_url.startswith('/title/tt'):
                  continue # Hoppa över om det inte ser ut som en filmtitel-länk

             result_year = None
             if year_tag:
                  # Försök extrahera år från texten (t.ex. "(2001)")
                  year_text = year_tag.get_text(strip=True)
                  year_match_imdb = re.search(r'\((\d{4})\)', year_text)
                  if year_match_imdb:
                       result_year = int(year_match_imdb.group(1))

             normalized_result_title = normalize_title(result_title_text)
             score = fuzz.token_set_ratio(normalized_search_title, normalized_result_title)

             logging.debug(f"    - IMDb Kandidat: '{result_title_text}', År: {result_year}, Score: {score}, URL: {result_url}")

             # Prioritering: Bra score, och om år angavs, ska det matcha hyfsat
             current_match_score = score
             is_good_match = False
             if year and result_year:
                  # Om år angavs, kräv att året matchar exakt eller nära OCH score är bra
                  if abs(year - result_year) <= 1 and score >= 75: # Högre krav här?
                       is_good_match = True
                       current_match_score += 100 # Ge bonus för årsmatchning
                  elif score >= 85 : # Tillåt om score är mycket hög även om året är fel?
                       is_good_match = True
             elif score >= 80: # Om inget år angavs, kräv hyfsad score
                  is_good_match = True

             if is_good_match and current_match_score > best_match_score:
                  best_match_score = current_match_score
                  best_match_link = result_url
                  logging.debug(f"      >> Ny bästa IMDb-match hittad!")

        if best_match_link:
            imdb_movie_url = "https://www.imdb.com" + best_match_link
            logging.debug(f"  > Bästa IMDb-match URL: {imdb_movie_url}")
        else:
            logging.warning(f"  > Ingen bra IMDb-match hittades för '{search_title}' ({year}).")
            return None

    except requests.exceptions.RequestException as e:
        logging.error(f"Nätverksfel vid sökning på IMDb: {e}")
        return None
    except Exception as e:
        logging.error(f"Fel vid parsing av IMDb sökresultat: {e}")
        return None

    # --- Steg 2: Skrapa IMDb-filmsidan för poster ---
    if imdb_movie_url:
        try:
            time.sleep(0.5) # Liten paus för att inte överbelasta IMDb
            logging.debug(f"  > Hämtar IMDb-sida: {imdb_movie_url}")
            response = requests.get(imdb_movie_url, headers=headers, timeout=15)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")

            # Hitta poster (GISSNINGAR på selectors - behöver verifieras!)
            poster_src = None
            # 1. Försök hitta via JSON-LD (ofta mer stabilt)
            json_ld_script = soup.find('script', type='application/ld+json')
            if json_ld_script:
                try:
                    json_data = json.loads(json_ld_script.string)
                    if isinstance(json_data, dict) and json_data.get('@type') == 'Movie' and json_data.get('image'):
                        poster_src = json_data['image']
                        if isinstance(poster_src, dict): poster_src = poster_src.get('url') # Ibland är det ett objekt
                        if poster_src and isinstance(poster_src, str):
                             logging.debug(f"  > IMDb Poster hittad via JSON-LD: {poster_src}")
                except Exception as json_e:
                    logging.debug(f"  > Kunde inte parsa JSON-LD: {json_e}")

            # 2. Försök via primär bild-selector (om JSON-LD misslyckas)
            if not poster_src:
                 # Anpassa selectorn! Kan vara t.ex. '[data-testid="hero-media__poster"] img'
                 poster_img = soup.select_one('div[class*="poster"] img[class*="ipc-image"]') # Gissning
                 if poster_img and poster_img.get('src'):
                      poster_src = poster_img['src']
                      logging.debug(f"  > IMDb Poster hittad via img selector: {poster_src}")

            # 3. Fallback till og:image
            if not poster_src:
                 og_image = soup.find('meta', property='og:image')
                 if og_image and og_image.get('content'):
                      poster_src = og_image['content']
                      logging.debug(f"  > IMDb Poster hittad via og:image: {poster_src}")

            if poster_src:
                 # IMDb ger ofta URL:er som funkar direkt
                 # Kan behöva rensa bort storleksparametrar? T.ex. allt efter '._V1_'
                 # poster_src = re.sub(r'\._V1_.*', '._V1_.jpg', poster_src) # Exempel på rensning
                 logging.info(f"  > IMDb Poster funnen: {poster_src}")
                 return poster_src
            else:
                 logging.warning(f"  > Kunde inte hitta poster på IMDb-sidan: {imdb_movie_url}")
                 return None

        except requests.exceptions.RequestException as e:
            logging.error(f"Nätverksfel vid hämtning av IMDb-filmsida: {e}")
            return None
        except Exception as e:
            logging.error(f"Fel vid parsing av IMDb-filmsida: {e}")
            return None
    else:
         return None # Ingen IMDb URL hittades i sökningen


# --- Kärnlogik (search_movie anropar nu IMDb för poster) ---

def search_movie(title, year=None):
    """Söker via HTML-skrapning och hämtar poster från IMDb."""
    params = {"s": title}
    headers = {'User-Agent': 'Mozilla/5.0'}

    initial_results = []
    normalized_input_title = normalize_title(title)
    input_year = None
    if year and year.isdigit(): input_year = int(year)

    logging.info(f"Startar HTML-skrapning för: '{title}', År: {year}")
    logging.debug(f"Normaliserad input: '{normalized_input_title}'")

    # --- Steg 1: Hämta kandidater från SFDb HTML ---
    try:
        response = requests.get(SEARCH_URL_HTML, params=params, headers=headers, timeout=15)
        response.raise_for_status()
        logging.debug(f"Hämtade HTML från: {response.url}")
        soup = BeautifulSoup(response.text, "html.parser")
        result_items = soup.select('ul.list li.list__item')
        logging.debug(f"Hittade {len(result_items)} list__item element.")

        for item in result_items:
            link_tag = item.select_one('a.list__link')
            heading_tag = item.select_one('h3.list__heading')
            if not link_tag or not heading_tag: continue
            href = link_tag.get('href')
            movie_title_with_year = heading_tag.get_text(strip=True)
            if not href or not movie_title_with_year: continue
            if 'type=film' not in href:
                type_tag = item.select_one('div.list__type')
                is_film_type = False
                if type_tag:
                    type_text = type_tag.get_text(strip=True).lower()
                    if 'film' in type_text or 'långfilm' in type_text or 'kortfilm' in type_text:
                        is_film_type = True
                if not is_film_type: continue
            if "trailer" in movie_title_with_year.lower(): continue
            item_id = extract_itemid_from_url(href)
            if not item_id: continue
            full_url = SFDB_BASE_URL + href if href.startswith('/') else href
            initial_results.append({'title_sv': movie_title_with_year, 'url': full_url, 'itemid': item_id})

    except requests.exceptions.RequestException as e:
        logging.error(f"Nätverksfel vid skrapning av SFDb HTML-sida: {e}")
        return []
    except Exception as e:
        logging.error(f"Oväntat fel vid skrapning av SFDb HTML: {e}")
        return []

    logging.info(f"Hittade {len(initial_results)} filmkandidater i HTML-listan.")
    if not initial_results: return []

    # --- Steg 2: Hämta Originaltitel (parallellt) & Beräkna Scores & Hämta IMDb Poster ---
    possible_matches = []
    # Använd ThreadPoolExecutor för att hämta SFDb originaltitel snabbare
    with ThreadPoolExecutor(max_workers=5) as executor_ot:
        future_to_movie_ot = {executor_ot.submit(get_sfdb_original_title, movie['url']): movie for movie in initial_results}
        processed_movies = {} # Spara resultat här med originaltitel

        for future in as_completed(future_to_movie_ot):
            initial_movie_data = future_to_movie_ot[future]
            try:
                original_title = future.result() # Hämta originaltitel
                movie_title_with_year = initial_movie_data['title_sv']
                logging.debug(f"\nBearbetar OT för: '{movie_title_with_year}'")

                normalized_swedish_title = normalize_title(movie_title_with_year)
                swedish_score = fuzz.token_set_ratio(normalized_input_title, normalized_swedish_title)
                logging.debug(f"  Score (SV): {swedish_score} ('{normalized_swedish_title}')")

                original_score = 0
                if original_title:
                    normalized_original_title = normalize_title(original_title)
                    original_score = fuzz.token_set_ratio(normalized_input_title, normalized_original_title)
                    logging.debug(f"  Score (OT): {original_score} ('{normalized_original_title}' from '{original_title}')")
                else:
                     logging.debug(f"  Score (OT): 0 (Ingen originaltitel hittades på SFDb)")

                score = max(swedish_score, original_score)
                logging.debug(f"  >> Max Score: {score}")

                found_year = None
                year_match = re.search(r'\((\d{4})\)$', movie_title_with_year.strip())
                if year_match: found_year = int(year_match.group(1))

                year_diff = float('inf')
                if input_year and found_year: year_diff = abs(input_year - found_year)
                logging.debug(f"  År: {found_year}, Årsdiff: {year_diff}")

                score_threshold = 65 # Behåll tröskeln
                if score >= score_threshold:
                    logging.debug(f"    >> PASSERADE TRÖSKEL ({score} >= {score_threshold})")
                    # Spara data för nästa steg (posterhämtning)
                    processed_movies[initial_movie_data['itemid']] = {
                        "title": movie_title_with_year,
                        "year": found_year,
                        "url": initial_movie_data['url'],
                        "itemid": initial_movie_data['itemid'],
                        "original_title": original_title, # Spara originaltitel för IMDb-sökning
                        "score": score,
                        "year_diff": year_diff
                    }
                else:
                    logging.debug(f"    >> IGNORERAD ({score} < {score_threshold})")

            except Exception as exc:
                logging.error(f"Fel vid bearbetning av OT-resultat för {initial_movie_data.get('url', 'Okänd URL')}: {exc}")

    logging.info(f"Hittade {len(processed_movies)} filmer som passerade tröskeln.")
    if not processed_movies: return []

    # --- Steg 3: Hämta IMDb-poster (parallellt) för de som passerade tröskeln ---
    with ThreadPoolExecutor(max_workers=3) as executor_poster: # Färre trådar för IMDb
        future_to_poster = {}
        for itemid, movie_data in processed_movies.items():
            title_for_imdb = movie_data['original_title'] if movie_data['original_title'] else movie_data['title']
            future = executor_poster.submit(get_imdb_poster, title_for_imdb, movie_data['year'])
            future_to_poster[future] = itemid

        for future in as_completed(future_to_poster):
            itemid = future_to_poster[future]
            movie_data = processed_movies[itemid] # Hämta tillbaka sparad data
            try:
                poster_url = future.result() # Hämta poster från IMDb
                movie_data['poster_url'] = poster_url # Lägg till postern
                possible_matches.append(movie_data) # Lägg till i slutliga listan
            except Exception as exc:
                logging.error(f"Fel vid hämtning av IMDb-poster för {itemid}: {exc}")
                # Lägg till ändå men utan poster?
                movie_data['poster_url'] = None
                possible_matches.append(movie_data)


    # Sortera resultaten baserat på data vi redan har
    if input_year:
        possible_matches.sort(key=lambda x: (x["year_diff"] != 0, x["year_diff"], -x["score"]))
    else:
        possible_matches.sort(key=lambda x: -x["score"])

    logging.info(f"Hittade {len(possible_matches)} slutliga matchningar för '{title}'.")

    # Returnera topp 6 resultat
    return possible_matches[:6]

# --- check_dcp_availability (Oförändrad) ---
def check_dcp_availability(movie_url):
    """Kontrollerar om 'DCP' nämns på filmens SFDb-sida."""
    # ... (samma kod som i förra svaret) ...
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(movie_url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        page_text = soup.get_text(separator=" ", strip=True).upper()
        if re.search(r'\bDCP\b', page_text):
             logging.info(f"DCP hittades på {movie_url}")
             return True
        else:
             dist_heading = soup.find('h2', id='companies') # Eller annan relevant ID/klass
             if dist_heading:
                 foldout_div = dist_heading.find_next_sibling('div', class_='accordion__foldout')
                 if foldout_div:
                     info_table = foldout_div.select_one('table.information-table')
                     if info_table:
                         rows = info_table.find_all('tr')
                         for row in rows:
                              th = row.find('th')
                              td = row.find('td')
                              if th and 'DCP' in th.get_text(strip=True).upper():
                                   logging.info(f"DCP hittades via specifik distributions-rad på {movie_url}")
                                   return True
                              if td and 'DCP' in td.get_text(strip=True).upper():
                                   logging.info(f"DCP hittades i distributions-td på {movie_url}")
                                   return True
             possible_sections = soup.select('div.technical-data, div.distribution-info, dl.attributes dt, dl.attributes dd')
             for section in possible_sections:
                 section_text = section.get_text(separator=" ", strip=True).upper()
                 if "DCP" in section_text:
                      logging.info(f"DCP hittades i möjlig teknisk/dist-sektion på {movie_url}")
                      return True
    except requests.exceptions.RequestException as e:
        logging.error(f"Nätverksfel vid kontroll av DCP för {movie_url}: {e}")
        return False
    except Exception as e:
        logging.error(f"Fel vid parsing/kontroll av DCP för {movie_url}: {e}")
        return False
    logging.info(f"DCP nämndes INTE på {movie_url}")
    return False


# --- Flask Routes (Nästan oförändrade, `details` behöver ej hämta poster) ---

@app.route('/', methods=['GET', 'POST'])
def index():
    """Hanterar huvudsidan med sökformulär och resultatlista."""
    if request.method == 'POST':
        movie_title = request.form.get('movie_title', '').strip()
        release_year = request.form.get('release_year', '').strip()
        if not movie_title:
             return render_template('index.html', movies=None, error="Du måste ange en filmtitel.")
        logging.info(f"Mottagen POST-request: Titel='{movie_title}', År='{release_year}'")
        movie_results = search_movie(movie_title, year=release_year) # Anropar nu IMDb-poster versionen
        if not movie_results:
            error_message = "Inga matchande filmer hittades."
            logging.info(f"Inga resultat funna för '{movie_title}'.")
            return render_template('index.html', movies=[], error=error_message, search_title=movie_title, search_year=release_year)
        else:
             logging.info(f"Visar {len(movie_results)} resultat för '{movie_title}'.")
             return render_template('index.html', movies=movie_results, error=None, search_title=movie_title, search_year=release_year)
    logging.debug("Visar tom söksida (GET request).")
    return render_template('index.html', movies=None, error=None, search_title='', search_year='')


@app.route('/details/<itemid>')
def details(itemid):
    """Visar detaljer och DCP-status för en specifik film."""
    # Not: Denna route hämtar INTE poster från IMDb igen.
    # Postern (eller None) borde ha hämtats i söksteget.
    # Om vi *vill* visa poster här måste vi antingen:
    # 1. Skicka med poster_url från index-sidan (via URL-parameter eller session - krångligt)
    # 2. Göra om IMDb-sökningen/skrapningen här IGEN (långsamt)
    # Vi väljer att visa detaljsidan UTAN poster för enkelhets skull.
    logging.info(f"Hämtar detaljer för itemid: {itemid}")
    if not itemid or not itemid.isdigit():
        logging.error(f"Ogiltigt itemid mottaget: {itemid}")
        return render_template('details.html', error="Ogiltigt film-ID.", movie_data=None)
    movie_url = f"{SFDB_BASE_URL}/sv/item/?type=film&itemid={itemid}"

    movie_title = f"Film (ID: {itemid})"
    # Försök hämta en bra titel
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(movie_url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        title_tag = soup.select_one('h1.page-header__heading')
        if title_tag:
            year_span = title_tag.find('span', class_='page-header__heading--release')
            if year_span: year_span.extract()
            h1_title = title_tag.get_text(strip=True)
            if h1_title and not h1_title.startswith("Film (ID:"): movie_title = h1_title
            logging.debug(f"Hittade titel för {itemid}: '{movie_title}'")
        else:
             # Försök med originaltitel som fallback om H1 misslyckas
             original_title_fallback = get_sfdb_original_title(movie_url)
             if original_title_fallback:
                 movie_title = original_title_fallback
                 logging.debug(f"Använder SFDb originaltitel för {itemid}: '{movie_title}'")
             else:
                 logging.warning(f"Kunde inte hitta h1-titel eller OT på {movie_url}")
    except Exception as e:
        logging.warning(f"Kunde inte hämta/parsea titel för {itemid} från {movie_url}: {e}")

    dcp_available = check_dcp_availability(movie_url)
    movie_data = {
        "title": movie_title,
        "url": movie_url,
        "poster_url": None, # Vi hämtar inte postern här för att undvika dubbel skrapning
        "itemid": itemid,
        "dcp_available": dcp_available
    }
    return render_template('details.html', movie_data=movie_data, error=None)

# --- App Execution ---

if __name__ == "__main__":
    # from concurrent.futures import ThreadPoolExecutor, as_completed # Behövs högst upp
    # import time # Behövs högst upp
    # from urllib.parse import quote_plus # Behövs högst upp

    debug_mode = os.environ.get("FLASK_DEBUG", "False").lower() in ["true", "1", "t"]
    port = int(os.environ.get("PORT", 5001))
    logging.info(f"Startar Flask-app på port {port} med debug={debug_mode}")
    app.run(host="0.0.0.0", port=port, debug=debug_mode)