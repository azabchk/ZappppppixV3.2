# ZappppppixV3.2

ZappppppixV3.2 is a simple trading engine and HTTP API built on top of FastAPI.  
It exposes endpoints for user registration, order management, balance management and administration of tradable instruments.  
The project provides a matching engine capable of handling limit and market orders with FIFO priority, atomic balance updates and predictable, deterministic responses.  
Everything is written in English and designed to run inside Docker.

## Features

* **User registration** – users can register and receive a unique API token.  
* **Order placement** – authenticated users can place buy and sell orders with market or limit semantics.  
* **Order book snapshot** – public endpoints allow anyone to view aggregated bid/ask levels for a given ticker.  
* **Transaction history** – executed trades are recorded and returned via a public endpoint.  
* **Balance management** – administrators can deposit and withdraw assets from any user.  
* **Instrument management** – administrators can add or remove instruments at runtime.  
* **Self‑test** – a Python script (`self_test.py`) exercises the core flows and verifies that the service behaves as expected.  

## Running the service

ZappppppixV3.2 is containerised.  To start the API and its backing PostgreSQL database, ensure Docker and docker‑compose are installed and then run:

```bash
docker-compose up --build
```

This command will build the API image, start a database container and expose the service on port `8000`.  The first startup will create the database schema, an administrator account and any default instruments defined via environment variables.

### Environment variables

The application is configured entirely via environment variables.  The following variables are recognised:

| Variable            | Description                                                      | Required | Default                     |
|---------------------|------------------------------------------------------------------|---------|-----------------------------|
| `DATABASE_URL`      | A full PostgreSQL connection string used by SQLAlchemy.          | **Yes** | –                           |
| `ADMIN_TOKEN`       | API token assigned to the built‑in administrator account.        | **Yes** | –                           |
| `DEFAULT_INSTRUMENTS` | Comma‑separated list of tickers to bootstrap on startup.        | No      | `RUB,USD`                   |

When running under Docker the `docker-compose.yml` file supplies reasonable defaults.  For local development you can copy `.env.example` to `.env` and customise the values as needed.  **Never commit an actual `.env` file containing secrets.**

### API overview

The API is versioned and organised into public, authenticated and administrator endpoints.  Below is a high‑level overview; see the OpenAPI documentation served by FastAPI at `/docs` for full details.

#### Public endpoints (no authentication)

* `POST /api/v1/public/register` – create a new user and return the generated API token.
* `GET /api/v1/public/instrument` – list all available instruments.
* `GET /api/v1/public/orderbook/{ticker}` – return an aggregated L2 order book snapshot for a ticker.
* `GET /api/v1/public/transactions/{ticker}` – return recent executed trades for a ticker.

#### Authenticated user endpoints

Authentication is performed via an `Authorization` header formatted as `TOKEN <apiKey>`.

* `GET /api/v1/balance` – return the caller’s balances.
* `POST /api/v1/order` – place a new market or limit order.
* `GET /api/v1/order` – list all orders placed by the caller.
* `GET /api/v1/order/{order_id}` – return a specific order belonging to the caller.
* `DELETE /api/v1/order/{order_id}` – cancel an open limit order (if allowed).

#### Administrator endpoints

All administrator endpoints require the caller’s user record to have the role `ADMIN`.  In addition to the normal authentication header, the caller’s role is verified.

* `POST /api/v1/admin/balance/deposit` – deposit a positive amount of an asset into a user’s account.
* `POST /api/v1/admin/balance/withdraw` – withdraw a positive amount of an asset from a user’s account if sufficient funds exist.
* `POST /api/v1/admin/instrument` – add a new instrument by ticker and type.
* `DELETE /api/v1/admin/instrument/{ticker}` – remove an instrument and all related balances, orders and trades.
* `DELETE /api/v1/admin/user/{user_id}` – delete a user and all their balances, orders and trades.

### Self‑test script

The `self_test.py` script can be used to verify that the core trading flows work correctly.  It registers two users, funds them via admin endpoints, executes a trade and checks balances, order statuses and the order book.  Run the test with:

```bash
python self_test.py
```

The script expects the API to be listening on `http://localhost:8000` and will exit with a non‑zero code if any assertions fail.

### Git initialisation instructions

When creating a new Git repository for this project you should ensure that only your account appears in the commit history.  The following steps initialise the repository and create a single initial commit:

1. Create a new empty folder and copy the contents of the `ZappppppixV3.2` directory into it.
2. Run `git init` inside the folder.
3. Add all files: `git add .`.
4. Create the initial commit with your own author details:
   ```bash
   git commit -m "Initial commit for ZappppppixV3.2" --author="Your Name <your.email@example.com>"
   ```
5. Add the remote for your new GitHub repository and push:
   ```bash
   git remote add origin <your-empty-repo-url>
   git branch -M main
   git push -u origin main
   ```

Following these steps results in a repository with a single commit authored by you, ensuring that no previous contributors appear in the commit history.
