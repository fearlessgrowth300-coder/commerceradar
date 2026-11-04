import { type NextRequest, NextResponse } from "next/server";

// Pages need a session cookie; the API validates it on every call (this is only a redirect).
export function middleware(req: NextRequest) {
  if (!req.cookies.get("cr_session")) {
    const url = req.nextUrl.clone();
    url.pathname = "/login";
    url.search = "";
    return NextResponse.redirect(url);
  }
}

export const config = { matcher: ["/((?!login|register|_next|favicon.ico).*)"] };
