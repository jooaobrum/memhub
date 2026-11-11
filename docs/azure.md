# memhub on Azure: Entra ID for Postgres and Azure OpenAI

memhub has no Azure code. On Azure, the app builds two things it already knows how to build:

- a psycopg **pool** whose password is an Entra ID token,
- **Azure OpenAI** chat and embedding models that authenticate with a token provider.

Then it passes them to `Memory`. memhub borrows connections from the pool, gives each one back open, and
never closes the pool. Pool size, SSL and token refresh stay with the app.

## Install

```bash
pip install memhub azure-identity langchain-openai
```

`azure-identity` and `langchain-openai` are the app's dependencies. memhub does not depend on them.

## The recipe

```python
import psycopg
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from langchain_openai import AzureChatOpenAI, AzureOpenAIEmbeddings
from psycopg_pool import ConnectionPool

from memhub import Memory

# az login, a service principal (AZURE_CLIENT_ID / AZURE_CLIENT_SECRET / AZURE_TENANT_ID), or a managed identity
cred = DefaultAzureCredential()


class EntraConnection(psycopg.Connection):
    """Every new connection logs in with a fresh Entra ID token as its password."""

    @classmethod
    def connect(cls, conninfo="", **kwargs):
        kwargs["password"] = cred.get_token("https://ossrdbms-aad.database.windows.net/.default").token
        return super().connect(conninfo, **kwargs)


pool = ConnectionPool(
    "host=my-server.postgres.database.azure.com dbname=memhub user=my-spn sslmode=require",
    connection_class=EntraConnection,
    max_lifetime=3000,  # seconds; under the token's 1-hour life, so pooled connections reconnect with a new token
    open=True,
)

token_provider = get_bearer_token_provider(cred, "https://cognitiveservices.azure.com/.default")

llm = AzureChatOpenAI(
    azure_endpoint="https://my-resource.openai.azure.com",
    azure_deployment="gpt-4.1-mini",
    api_version="2024-10-21",
    azure_ad_token_provider=token_provider,
)
embedder = AzureOpenAIEmbeddings(
    azure_endpoint="https://my-resource.openai.azure.com",
    azure_deployment="text-embedding-3-small",
    api_version="2024-10-21",
    azure_ad_token_provider=token_provider,
)

m = Memory(db=pool, llm=llm, embedder=embedder)
m.init()  # once per database; safe to run again
```

## Why `max_lifetime` matters

An Entra ID token for Postgres lasts about one hour. The token is only checked at login, so an open
connection keeps working after its token expires. A connection the pool opens *later* needs a valid
token. `max_lifetime=3000` retires each pooled connection after 50 minutes. The pool then opens a new
one through `EntraConnection.connect`, which fetches a fresh token. Keep `max_lifetime` under 3600.

## The Postgres side

- Turn on Microsoft Entra authentication on the flexible server, and add the service principal or managed
  identity as an Entra admin, or as a role created with `pgaadauth_create_principal`.
- Allow the `vector` extension: add `VECTOR` to the server's `azure.extensions` parameter.
  `m.init()` then runs `CREATE EXTENSION IF NOT EXISTS vector`.
- The `user=` in the connection string is the principal's name as Postgres knows it, for example the app
  registration's display name.

## The embedding model id

`m.init()` stores the embedding model's id and the dimensions it returns. A later `Memory` with another
embedder is refused with `EmbeddingMismatch`. With no config, the id comes from the embedder's `model`
attribute. If you pass a config with an `embeddings.model`, that name is used instead. Keep the same name
the CLI uses, so `memhub` commands and the app agree on one ledger.
