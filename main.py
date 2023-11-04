from chromadb import PersistentClient  # https://docs.trychroma.com/usage-guide
from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction
import anthropic
from os import environ  # for secrets
from flask import Flask, request, render_template
from twilio.rest import Client
from twilio.request_validator import RequestValidator
import threading
from time import sleep

OPENAI_API_KEY = environ['OPENAI_KEY']
embed_fn = OpenAIEmbeddingFunction(api_key=OPENAI_API_KEY,
   model_name="text-embedding-ada-002")  # current OpenAI embeddings
MAX_EMBED_LEN = 30000

client = PersistentClient(path="chroma-db")
vectordb = client.get_or_create_collection("ucsc-docs", embedding_function=embed_fn)  # changed db name

CLAUDE_API_KEY = environ['CLAUDE_KEY']
PROMPT_MAX_LEN = 50000

# Get a response from the Anthropic Claude LLM
def claude(prompt): # see https://github.com/anthropics/anthropic-sdk-python
    llm = anthropic.Client(api_key=CLAUDE_API_KEY)

    for attempt in range(20): # retry rate limits and server overloads twenty times
      try:
          result = llm.completions.create(temperature=0.0, # 0 for quasi-reproducibility
              max_tokens_to_sample=5000, # maximum response tokens, ~20,000 words
              prompt=f'{anthropic.HUMAN_PROMPT} {prompt}{anthropic.AI_PROMPT}',
              stop_sequences=[anthropic.HUMAN_PROMPT], model='claude-instant-1.2') # or claude-v1.3 or claude-2
              # see https://docs.anthropic.com/claude/reference/complete_post
    
          return result.completion.strip() # return Claude's response
    
      except anthropic.RateLimitError as err: # not yet encountered even when simultanious
          print(f"!!! Claude API error {err.status_code}: {err.response.text}, "
                 "retrying {19 - attempt} times....")
      except anthropic.InternalServerError as err: # i.e. 529: temporarily overloaded
          print(f"!!! Claude API error {err.status_code}: {err.response.text}, "
                 "retrying {19 - attempt} times....") # also haven't ever seen yet
      sleep(2) # pause for rate limits and overloadings; everything else is uncaught
    
    # If all retry attempts are exhausted, raise RuntimeError exception to halt
    raise RuntimeError("Oops! Something went wrong while we were trying to complete your action. Don't worry, it's not your fault. Please try refreshing the page or check back in a few minutes. If the issue persists, contact our support team for help. We apologize for the inconvenience.") # TODO: does this even get to the web user?

def ask(question):
    # Look up pertinent docs
    docs = vectordb.query(query_texts=[question[:MAX_EMBED_LEN]], 
                          n_results=20)['documents'][0]  ### ???: is 20 a good number?

    intro = 'Use the following information about UC Santa Cruz programs to help answer the question below.'
    
    # Follow introduction by as many retrieved docs as can fit
    for doc in docs:
        if len(intro + question + doc) > PROMPT_MAX_LEN:
            break
        else:
            intro += '\n\n-----\n' + doc
    
    #return the call to Claude
    return claude(intro + '\n\n-----\n\nQUESTION: ' + question)

                         
account_sid = environ['TWILIO_SID']  
auth_token = environ['TWILIO_TOKEN']  

def sms_reply(message, phone):
  print('SMS subthread:', phone, message)
  if message.lower().startswith('slugbot '):
    reply = ask(message.split(maxsplit=1)[1])
  #elif message.lower().startswith('issue '):
  #  reply = solve_pertinent(message.split(maxsplit=1)[1])
  else:
    reply = 'Type "slugbot" followed by your question about UCSC programs.'

  client = Client(account_sid, auth_token)
    
  conted = False
  while len(reply) > 1500:  # too long for a single SMS from Twilio
    client.messages.create(from_='+18778306766', to=phone,
                           body=('...' if conted else '') +  # add elipses if continued
                           reply[:1500] + '... [continued]')
    reply = reply[1500:]  # process remainder of message
    conted = True  # next message is continued
    print('subthread sent 1500 character message')
    sleep(5)  # try to keep messages in order

  client.messages.create(body=('...' if conted else '')  # add elipses if continued
                         + reply, from_='+18778306766', to=phone)

  print('subthread ended with', len(reply), 'character message')

app = Flask(__name__)

@app.route('/', methods=['POST', 'GET'])
def index():
    if request.method == 'GET': 
        return render_template('index.html', vectordb=vectordb)
    else:
        if request.form.get('problem'):
            return ('<h3>' + request.form.get('problem') + '</h3>\n' +
                    '<p style="white-space: pre-wrap;">\n' +
                    ask(request.form.get('problem')) +
                    '\n</p><a href="/">Ask another question</a>\n')
        else:
            return render_template('index.html', vectordb=vectordb)


@app.route("/sms", methods=['POST'])
def incoming_sms():
    # Validate that the request was from Twilio
    validator = RequestValidator(auth_token)
    
    # Extract the X-Twilio-Signature header from the request
    signature = request.headers.get('X-TWILIO-SIGNATURE', '')
    
    # Use the validator to check the request against the signature
    # Note: request.url includes the full URL and query parameters
    is_valid = validator.validate(request.url, request.form, signature)
    
    if not is_valid:
        #print('invalid /sms - ignoring for now')  ### TODO: see https://www.twilio.com/docs/usage/tutorials/how-to-secure-your-django-project-by-validating-incoming-twilio-requests
        #return 'INVALID: "SMS" not from Twilio!', 400
        pass  ### no idea how to fix this really; maybe https://stackoverflow.com/questions/76625736/twilio-requestvalidator-not-working-in-python-wsgi
    
    # Get the message the user sent our Twilio number
    body = request.values.get('Body', None)
    phone = request.values.get('From')

    print('SMS request from', phone, body)
    
    # new thread here, as a coprocessing thread to prevent timeout
    thread = threading.Thread(target=sms_reply, args=(body, phone))
    thread.start()

    return '<Response></Response>'  # return immediately to prevent timeout

app.run(host='0.0.0.0', port=81)
