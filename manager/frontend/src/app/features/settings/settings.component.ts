import { Component } from '@angular/core';
import { MatCardModule } from '@angular/material/card';
import { MatIconModule } from '@angular/material/icon';

@Component({
  selector: 'app-settings',
  standalone: true,
  imports: [MatCardModule, MatIconModule],
  template: `
    <h1>Settings</h1>
    <mat-card>
      <mat-card-content>
        <div class="placeholder">
          <mat-icon>settings</mat-icon>
          <p>Settings configuration will be available in a future release.</p>
        </div>
      </mat-card-content>
    </mat-card>
  `,
  styles: [`
    .placeholder {
      display: flex;
      flex-direction: column;
      align-items: center;
      padding: 48px;
      color: #999;
    }
    .placeholder mat-icon {
      font-size: 48px;
      height: 48px;
      width: 48px;
    }
  `],
})
export class SettingsComponent {}
