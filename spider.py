from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from urllib.parse import urljoin, urlparse, urlunparse
from chromadb import PersistentClient  # https://docs.trychroma.com/usage-guide
from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction
from os import environ  # for secrets

spider = ['https://ches.ucsc.edu/peer-review-board/index.html']  # list of URL (s)
#limit = '/peer-review-board/'  # only spider links with this substring !!!? Takes 2.3 minutes to get 5 pages
limit = '//ches.ucsc.edu'  ### takes 5.25 minutes to get 44 pages
### was: https://registrar.ucsc.edu/enrollment/part-time-program/

def scrape():
    # Setup chrome options
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")  ### uncomment if selenium crashes out of memory
    # Setup service
    service = Service() # was '/usr/local/bin/chromedriver' but not for replit
    # Initialize the driver
    driver = webdriver.Chrome(service=service, options=chrome_options)

    pages = {} # url -> title\n\ntext

    # Go to the site
    driver.get(spider[0])

    # Add cookies
    #driver.add_cookie({'name' : '_sso.key', 'value' : 'Nc61G9cnhtYVdhp2bbiscdMQ6lTvaWRL', 'domain' : '.startupschool.org'})
    
    while spider:
        url = spider.pop(0)
        print('spidering', url)
    
        if url[-4:].lower() in ['.pdf', '.mp4', '.mp3']:
            print('*** skipping .PDF/media')  ### TODO: use pdfminer, don't download
            continue
    
        # Refresh the page to use the added cookies
        driver.get(url)
    
        # Give it time to load the text
        try:
            WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.CLASS_NAME, 'main-content')))
        except:
            print('***', driver.current_url, 'failed to load main-content')
            continue
    
        if driver.current_url in pages:
            print('--- already seen')
            continue
    
        text = driver.find_element(By.CLASS_NAME, 'main-content').text  ###!!! not fully general

        # save the text
        text = driver.title + '\n\n' + text
        pages[driver.current_url] = text
        print('--- saved', len(text), 'characters:', driver.title)
        
        # Get new URLs to spider breadth-first
        elements = driver.find_elements(By.TAG_NAME, 'a')  ###!!! misses buttons etc.
        for element in elements:
            if element.get_attribute('href'):
                turl = element.get_attribute('href')
                if turl[:4] != 'http':  # relative links
                    turl = urljoin(driver.current_url, turl)
                nurl = urlunparse(urlparse(turl)._replace(fragment=""))  # remove #section suffixes
                if limit in nurl and nurl not in pages and nurl not in spider:
                        spider.append(nurl)
                        print('adding url:', nurl)
        
    # Close the driver
    driver.quit()
    
    print(len(pages), 'pages spidered')
    
    with open('spider.txt', 'w') as f:  # write the saved text
        f.write(repr(pages))

    OPENAI_API_KEY = environ['OPENAI_KEY']  # for embeddings
    embed_fn = OpenAIEmbeddingFunction(api_key=OPENAI_API_KEY,
       model_name="text-embedding-ada-002")  # current OpenAI embeddings
    
    MAX_EMBED_LEN = 30000
    
    client = PersistentClient(path="chroma-db")
    vectordb = client.get_or_create_collection("ucsc-docs", embedding_function=embed_fn)
    
    vectordb.delete(where={"url": {"$ne": "### DELETE EVERYTHING"}})  # clear database
    
    for url, text in pages.items():
        
        metadata = {'url': url}
    
        vectordb.add(documents=[text[:MAX_EMBED_LEN]], metadatas=[metadata],
                ids=[str(vectordb.count() + 1)])  # auto-increment, base one

scrape()  # run the above
