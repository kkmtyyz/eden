"use client";

import { Authenticator } from "@aws-amplify/ui-react";
import "@aws-amplify/ui-react/styles.css";
import { configureAmplify } from "./amplify-config";

// Amplify 初期化
configureAmplify();

export function AmplifyProvider({ children }: { children: React.ReactNode }) {
  return (
    <Authenticator
      hideSignUp={true}
      variation="modal"
      components={{
        Header() {
          return (
            <div style={{ textAlign: "center", padding: "1rem" }}>
              <h2>Eden</h2>
              <p style={{ fontSize: "0.875rem", color: "#666" }}>
                招待されたユーザーのみログインできます
              </p>
            </div>
          );
        },
      }}
    >
      {children}
    </Authenticator>
  );
}
